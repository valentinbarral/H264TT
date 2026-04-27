import sys
import os
import subprocess
import re
import json
import cv2  # type: ignore[attr-defined]


NATIVE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "native"))
MOTION_VECTOR_EXTRACTOR_SOURCE = os.path.join(NATIVE_DIR, "extract_mvs.c")
MOTION_VECTOR_EXTRACTOR_BINARY = os.path.join(
    NATIVE_DIR, "extract_mvs.exe" if sys.platform == "win32" else "extract_mvs"
)


def create_yuv_from_mp4(
    input_mp4, output_yuv, width=720, height=398, fps=25, ffmpeg_path="ffmpeg"
):
    """
    Convierte un MP4 a YUV raw con parámetros específicos.
    """
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        input_mp4,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "yuv420p",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        output_yuv,
    ]

    print(f"Convirtiendo {input_mp4} a {output_yuv}...")
    print(f"Comando: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        # Verificar integridad
        frame_size = width * height * 3 // 2
        file_size = os.path.getsize(output_yuv)
        frames = file_size // frame_size
        remainder = file_size % frame_size

        print(f"✅ Conversión exitosa:")
        print(f"   - Tamaño archivo: {file_size:,} bytes")
        print(f"   - Frames: {frames}")
        print(f"   - Duración: {frames / fps:.2f} segundos")
        print(f"   - Bytes restantes: {remainder}")

        if remainder == 0:
            print("✅ Archivo YUV íntegro (sin bytes restantes)")
        else:
            print("⚠️  Archivo YUV puede tener frames incompletos")

        return True
    else:
        print(f"❌ Error en conversión: {result.stderr}")
        return False


class MBVisualizer:
    def __init__(
        self,
        input_file,
        output_prefix,
        ffmpeg_params="-c:v libx264 -preset medium -crf 23",
        output_video_name=None,
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
        generate_plot_image=True,
        keep_debug_log=True,
    ):
        self.input_file = input_file
        self.output_prefix = output_prefix
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.ffmpeg_params = ffmpeg_params.split()
        self.generate_plot_image = generate_plot_image
        self.keep_debug_log = keep_debug_log

        # Detectar si es un archivo YUV raw
        self.is_yuv_file = (
            input_file.lower().endswith((".yuv", ".y4m"))
            or "-f rawvideo" in ffmpeg_params
        )

        # Determinar nombres de los videos de salida
        if output_video_name:
            # Si el usuario da un nombre, ese será el video limpio
            self.output_mp4 = output_video_name
            root, ext = os.path.splitext(output_video_name)
            artifact_base = root
        else:
            # Default basado en prefijo
            self.output_mp4 = f"{output_prefix}_encoded.mp4"
            artifact_base = output_prefix

        self.output_encoded = (
            f"{artifact_base}_encoded.h264"  # Stream raw intermedio para análisis
        )
        self.log_file = f"{artifact_base}_mb_types.txt"
        self.analysis_file = f"{artifact_base}.analysis.json"

        # Mapa de colores (BGR format for OpenCV)
        # Rojo: Intra (Costoso/Detalle)
        # Verde: Skip (Muy eficiente/Estático)
        # Azul: Inter (Predicción temporal - P/B standard)
        self.colors = {
            "INTRA": (0, 0, 200),  # Rojo oscuro
            "SKIP": (0, 180, 0),  # Verde
            "INTER": (200, 0, 0),  # Azul
        }

        # Contadores para estadísticas de macroblocks
        self.mb_stats = {
            "total_mb": 0,
            "by_type": {"INTRA": 0, "SKIP": 0, "INTER": 0},
            "by_frame_type": [],  # Lista de diccionarios por frame
        }

        # Contadores para estadísticas de tipos de frame
        self.frame_stats = {
            "total_frames": 0,
            "by_type": {"I": 0, "P": 0, "B": 0},
            "frame_types": [],  # Lista con el tipo de cada frame
        }

        # Datos para gráficas
        self.frame_data = {
            "frame_numbers": [],
            "bitrate_values": [],
            "qp_values": [],
            "motion_vectors": [],
        }

    def _ensure_motion_vector_extractor(self):
        if not os.path.exists(MOTION_VECTOR_EXTRACTOR_SOURCE):
            print(
                f"Advertencia: no se encontró el extractor de motion vectors: {MOTION_VECTOR_EXTRACTOR_SOURCE}"
            )
            return None

        # Buscar binario preexistente (con o sin extensión de plataforma)
        binary_names = [MOTION_VECTOR_EXTRACTOR_BINARY]
        if sys.platform == "win32":
            binary_names.append(MOTION_VECTOR_EXTRACTOR_BINARY.replace(".exe", ""))
        else:
            binary_names.append(MOTION_VECTOR_EXTRACTOR_BINARY + ".exe")

        source_mtime = os.path.getmtime(MOTION_VECTOR_EXTRACTOR_SOURCE)
        for candidate in binary_names:
            if os.path.exists(candidate):
                binary_mtime = os.path.getmtime(candidate)
                if binary_mtime >= source_mtime:
                    return candidate

        pkg_flags = self._get_ffmpeg_compile_flags()
        if pkg_flags is None:
            return None

        compile_cmd = [
            "gcc",
            MOTION_VECTOR_EXTRACTOR_SOURCE,
            "-O2",
            "-std=c11",
            "-o",
            MOTION_VECTOR_EXTRACTOR_BINARY,
        ] + pkg_flags

        try:
            subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
            return MOTION_VECTOR_EXTRACTOR_BINARY
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = getattr(e, "stderr", "")
            print(
                f"Advertencia: no se pudo compilar el extractor de motion vectors: {e}\n{stderr}"
            )
            return None

    def _get_ffmpeg_compile_flags(self):
        """Obtiene flags de compilación para FFmpeg, probando pkg-config y pkgconf."""
        for pkg_tool in ("pkg-config", "pkgconf"):
            try:
                result = subprocess.run(
                    [
                        pkg_tool,
                        "--cflags",
                        "--libs",
                        "libavformat",
                        "libavcodec",
                        "libavutil",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                return result.stdout.split()
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue

        print(
            "Advertencia: no se encontró pkg-config ni pkgconf. "
            "Instala FFmpeg dev libraries y pkg-config para motion vectors."
        )
        return None

    def run_step(self, message):
        print(f"[*] {message}...")

    def check_ffmpeg(self):
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            print("Error: FFmpeg no se encuentra en el PATH. Instálalo para continuar.")
            sys.exit(1)

        version_output = result.stdout.splitlines()[0] if result.stdout else ""
        if "ffmpeg version" in version_output:
            print(f"FFmpeg detectado: {version_output}")

            major_version_match = re.search(r"ffmpeg version (\d+)", version_output)
            if major_version_match:
                major_version = int(major_version_match.group(1))
                if major_version > 6:
                    print(
                        "⚠️  Aviso: esta herramienta está pensada para FFmpeg 6.1.1; versiones más nuevas pueden no exponer igual los datos de bajo nivel."
                    )

    def encode_video(self):
        self.run_step("Codificando video con parámetros definidos")

        if self.is_yuv_file:
            # Para archivos YUV raw, separar parámetros de entrada y codificación
            input_params = []
            output_params = []

            i = 0
            while i < len(self.ffmpeg_params):
                param = self.ffmpeg_params[i]
                # Parámetros de entrada (van antes del -i)
                if param in ["-f", "-s", "-r", "-pix_fmt", "-framerate"]:
                    input_params.extend([param, self.ffmpeg_params[i + 1]])
                    i += 2
                # El resto son parámetros de salida
                else:
                    output_params.append(param)
                    i += 1

            # 1. Crear versión MP4 para visualizar desde YUV raw
            cmd = (
                [self.ffmpeg_path, "-y", "-debug", "qp"]
                + input_params
                + ["-i", self.input_file]
                + output_params
                + [self.output_mp4]
            )
        else:
            # Para archivos de video normales, usar el flujo original
            cmd = (
                [self.ffmpeg_path, "-y", "-debug", "qp", "-i", self.input_file]
                + self.ffmpeg_params
                + [self.output_mp4]
            )

        # 2. Extraer raw H.264. Es CRUCIAL analizar el bitstream raw, no el mp4
        #    porque el filtro de debug trabaja a nivel de codec.
        cmd_raw = [
            self.ffmpeg_path,
            "-y",
            "-i",
            self.output_mp4,
            "-c",
            "copy",
            "-bsf:v",
            "h264_mp4toannexb",
            "-f",
            "h264",
            self.output_encoded,
        ]

        full_cmd = " ".join(cmd)
        print(f"Comando de codificación: {full_cmd}")

        # Capturar salida completa para extraer QP real
        print("Capturando salida de FFmpeg para extraer QP...")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"Salida stdout tiene {len(result.stdout)} caracteres")
        print(f"Salida stderr tiene {len(result.stderr)} caracteres")

        # Los mensajes QP pueden estar en stdout o stderr
        combined_output = result.stdout + result.stderr

        # Extraer valores QP de la salida de FFmpeg
        self.extract_qp_from_encoding_output(combined_output)

        # Verificar que el video se generó correctamente
        if not os.path.exists(self.output_mp4):
            print(f"Error: El video no se generó: {self.output_mp4}")
            sys.exit(1)

        video_size = os.path.getsize(self.output_mp4)
        if video_size < 1000:  # Menos de 1KB es sospechoso
            print(f"Advertencia: El video generado es muy pequeño: {video_size} bytes")

        subprocess.run(cmd_raw, check=True)

    def extract_debug_info(self):
        self.run_step(
            "Extrayendo tipos de macrobloques y métricas por frame (debug mb_type)"
        )
        # Comando específico para versiones modernas de FFmpeg que aún soportan debug mb_type
        # Usamos el stream .h264 raw creado anteriormente
        cmd = [
            self.ffmpeg_path,
            "-threads",
            "1",  # Importante: 1 hilo para asegurar orden secuencial en el log
            "-debug",
            "mb_type",
            "-i",
            self.output_encoded,
            "-f",
            "null",  # No queremos salida de video, solo el log
            "-",
        ]

        print(f"    Ejecutando: {' '.join(cmd)} 2> {self.log_file}")

        # FFmpeg escribe el debug info en STDERR
        with open(self.log_file, "w", encoding="utf-8") as f_log:
            result = subprocess.run(cmd, stderr=f_log)

        if result.returncode != 0:
            print(
                "Advertencia: FFmpeg terminó con código de error, pero intentaremos procesar el log."
            )

        # Extraer métricas por frame después de procesar el log
        self.extract_frame_metrics()

    def extract_qp_from_encoding_output(self, ffmpeg_output):
        """Extrae valores QP y SIZE reales de la salida de FFmpeg durante la codificación."""
        qp_by_frame = {}
        log_frame_types = {}
        lines_with_qp = 0
        lines_with_frame = 0

        for line in ffmpeg_output.split("\n"):
            if "QP=" in line:
                lines_with_qp += 1
            if "frame=" in line:
                lines_with_frame += 1

            # Buscar líneas como: [libx264 @ 0x...] frame= 207 QP=12.22 NAL=0 Slice:B Poc:12 I:25 P:510 SKIP:590 size=1412 bytes
            if (
                "QP=" in line
                and "frame=" in line
                and "size=" in line
                and "Slice:" in line
            ):
                try:
                    frame_match = re.search(r"frame=\s*(\d+)", line)
                    # Extraer el valor QP
                    qp_match = re.search(r"QP=([\d.]+)", line)
                    # Extraer el tipo de frame (Slice:I, Slice:P, Slice:B)
                    slice_match = re.search(r"Slice:([IPB])", line)

                    if frame_match and qp_match and slice_match:
                        frame_num = int(frame_match.group(1))
                        qp_value = float(qp_match.group(1))
                        frame_type = slice_match.group(1)

                        qp_by_frame[frame_num] = qp_value
                        log_frame_types[frame_num] = frame_type

                        if len(qp_by_frame) <= 10:  # Mostrar primeros 10 para debug
                            print(
                                f"FRAME={frame_num}, QP={qp_value}, TYPE={frame_type} en línea: {line.strip()}"
                            )
                    else:
                        print(
                            f"Línea QP incompleta (faltan frame/QP/Slice): {line.strip()}"
                        )
                except (ValueError, IndexError) as e:
                    print(
                        f"Error parseando frame/QP/TYPE en línea: {line.strip()} - {e}"
                    )
                    continue

        print(
            f"Análisis de salida: {lines_with_frame} líneas con 'frame=', {lines_with_qp} líneas con 'QP='"
        )

        if qp_by_frame:
            max_frame_num = max(qp_by_frame)
            qp_values = [qp_by_frame.get(idx) for idx in range(max_frame_num + 1)]
            frame_types = [
                log_frame_types.get(idx, "U") for idx in range(max_frame_num + 1)
            ]

            print(
                f"Extraídos {len(qp_values)} valores QP y {len(frame_types)} tipos de frame de la codificación"
            )
            print(f"Primeros 10 valores QP: {qp_values[:10]}")
            print(f"Primeros 10 tipos de frame: {frame_types[:10]}")
            self.frame_data["qp_values"] = qp_values
            self.frame_data["frame_numbers"] = list(range(max_frame_num + 1))
            self.frame_stats["frame_types"] = frame_types
        else:
            print("No se encontraron valores QP en la salida de codificación")
            # Mostrar algunas líneas de ejemplo para debug
            lines = ffmpeg_output.split("\n")
            sample_lines = [line for line in lines if line.strip()][
                :10
            ]  # Primeras 10 líneas no vacías
            print("Primeras líneas de la salida:")
            for i, line in enumerate(sample_lines):
                print(f"  {i + 1}: {line.strip()}")

        # Eliminar el archivo H.264 raw ya que no se necesita más
        try:
            if os.path.exists(self.output_encoded):
                os.remove(self.output_encoded)
                print(f"Archivo H.264 temporal eliminado: {self.output_encoded}")
        except OSError as e:
            print(f"Advertencia: No se pudo eliminar el archivo H.264 temporal: {e}")

    def extract_frame_metrics(self):
        """Extrae métricas por frame (bitrate, QP) usando FFmpeg."""
        self.run_step("Extrayendo métricas por frame (bitrate, QP)")

        # Extraer QP y bitrate del archivo MP4 usando ffprobe
        self.extract_qp_bitrate_data()
        self.extract_motion_vector_data()

    def extract_qp_bitrate_data(self):
        """Extrae tamaño, bitrate y tipo de frame del MP4 usando ffprobe."""
        try:
            import json

            # Usar ffprobe para extraer información detallada por frame
            probe_cmd = [
                self.ffprobe_path,
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=pkt_size,pkt_pts_time,pict_type",
                "-of",
                "json",
                self.output_mp4,
            ]

            result = subprocess.run(probe_cmd, capture_output=True, text=True)

            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    frames = data.get("frames", [])
                    bitrate_values = []
                    size_values = []
                    frame_types = []

                    for frame in frames:
                        pkt_size = frame.get("pkt_size")
                        pict_type = frame.get("pict_type")

                        if pkt_size is not None:
                            try:
                                pkt_size_int = int(pkt_size)
                                size_values.append(pkt_size_int)
                                frame_types.append(
                                    pict_type if pict_type in ["I", "P", "B"] else "U"
                                )

                                # Bitrate aproximado (bytes por segundo promedio)
                                # Como no tenemos tiempo preciso para cada frame, usamos una aproximación
                                avg_bitrate = (
                                    pkt_size_int * 25 * 8
                                )  # 25fps * 8 bits por byte
                                bitrate_values.append(avg_bitrate)

                            except (ValueError, TypeError):
                                continue

                    if size_values:
                        self.frame_data["size_values"] = size_values
                        self.frame_data["bitrate_values"] = bitrate_values
                        self.frame_stats["frame_types"] = frame_types

                        qp_values = self.frame_data.get("qp_values", [])
                        usable_frames = min(
                            len(qp_values), len(size_values), len(frame_types)
                        )
                        if usable_frames > 0 and self.generate_plot_image:
                            self.create_qp_size_plots(
                                qp_values[:usable_frames],
                                size_values[:usable_frames],
                                frame_types[:usable_frames],
                            )

                except json.JSONDecodeError:
                    print("Advertencia: Error parseando JSON de ffprobe")

        except Exception as e:
            print(f"Advertencia: No se pudieron extraer métricas de bitrate: {e}")

    def extract_motion_vector_data(self):
        """Extrae motion vectors por frame usando un helper basado en libavcodec."""
        extractor_path = self._ensure_motion_vector_extractor()
        if extractor_path is None:
            self.frame_data["motion_vectors"] = []
            return

        try:
            result = subprocess.run(
                [extractor_path, self.output_mp4],
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            stderr = getattr(e, "stderr", "")
            print(f"Advertencia: no se pudieron extraer motion vectors: {e}\n{stderr}")
            self.frame_data["motion_vectors"] = []
            return

        motion_vectors_by_frame = {}
        total_vectors = 0

        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("frame_index,"):
                continue

            parts = line.split(",")
            if len(parts) != 11:
                continue

            try:
                frame_index = int(parts[0])
                vector_data = {
                    "source": int(parts[1]),
                    "w": int(parts[2]),
                    "h": int(parts[3]),
                    "src_x": int(parts[4]),
                    "src_y": int(parts[5]),
                    "dst_x": int(parts[6]),
                    "dst_y": int(parts[7]),
                    "motion_x": int(parts[8]),
                    "motion_y": int(parts[9]),
                    "motion_scale": int(parts[10]),
                }
            except ValueError:
                continue

            motion_vectors_by_frame.setdefault(frame_index, []).append(vector_data)
            total_vectors += 1

        frame_count = max(
            len(self.frame_data.get("size_values", [])),
            len(self.frame_data.get("qp_values", [])),
            (max(motion_vectors_by_frame.keys()) + 1) if motion_vectors_by_frame else 0,
        )
        self.frame_data["motion_vectors"] = [
            motion_vectors_by_frame.get(frame_idx, [])
            for frame_idx in range(frame_count)
        ]
        print(
            f"Extraídos {total_vectors} motion vectors repartidos en {len(motion_vectors_by_frame)} frames"
        )

    def calculate_psnr(self):
        """Calcula el PSNR comparando el video original con el comprimido."""
        try:
            if self.is_yuv_file:
                # Para archivos YUV: convertir el MP4 comprimido a YUV y comparar
                # Primero extraer las dimensiones del YUV original de los parámetros
                yuv_params = []
                width = height = None

                i = 0
                while i < len(self.ffmpeg_params):
                    param = self.ffmpeg_params[i]
                    if param == "-s":
                        # Formato: 720x398
                        size_parts = self.ffmpeg_params[i + 1].split("x")
                        if len(size_parts) == 2:
                            width, height = int(size_parts[0]), int(size_parts[1])
                        i += 2
                    else:
                        i += 1

                if width and height:
                    # Crear archivo YUV temporal del video comprimido
                    temp_yuv = f"{self.output_prefix}_temp.yuv"
                    cmd_extract = [
                        self.ffmpeg_path,
                        "-y",
                        "-i",
                        self.output_mp4,
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "yuv420p",
                        "-s",
                        f"{width}x{height}",
                        temp_yuv,
                    ]

                    # Ejecutar extracción
                    result_extract = subprocess.run(
                        cmd_extract, capture_output=True, text=True
                    )
                    if result_extract.returncode != 0:
                        return None

                    # Calcular PSNR comparando YUV original vs YUV del comprimido
                    cmd_psnr = [
                        self.ffmpeg_path,
                        "-y",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "yuv420p",
                        "-s",
                        f"{width}x{height}",
                        "-r",
                        "25",  # Asumir 25fps por defecto
                        "-i",
                        self.input_file,
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "yuv420p",
                        "-s",
                        f"{width}x{height}",
                        "-r",
                        "25",
                        "-i",
                        temp_yuv,
                        "-filter_complex",
                        "[0:v][1:v]psnr",
                        "-f",
                        "null",
                        "-",
                    ]

                    result_psnr = subprocess.run(
                        cmd_psnr, capture_output=True, text=True
                    )

                    # Limpiar archivo temporal
                    try:
                        os.remove(temp_yuv)
                    except:
                        pass

                    # Extraer PSNR del output
                    for line in result_psnr.stderr.split("\n"):
                        if "PSNR" in line and "average" in line:
                            # Formato típico: "PSNR y:32.45 u:41.23 v:42.34 average:35.67"
                            parts = line.split()
                            for part in parts:
                                if part.startswith("average:"):
                                    try:
                                        return float(part.split(":")[1])
                                    except:
                                        continue
                    # Si no se encontró PSNR en el output, retornar None
                    return None
            else:
                # Para archivos YUV: si no se pudieron extraer las dimensiones
                print(
                    "Advertencia: No se pudieron extraer las dimensiones del archivo YUV"
                )
                return None

            # Para archivos de video normales: usar FFmpeg directamente
            cmd_psnr = [
                self.ffmpeg_path,
                "-y",
                "-i",
                self.input_file,
                "-i",
                self.output_mp4,
                "-filter_complex",
                "[0:v][1:v]psnr",
                "-f",
                "null",
                "-",
            ]

            result_psnr = subprocess.run(cmd_psnr, capture_output=True, text=True)

            # Extraer PSNR del output
            for line in result_psnr.stderr.split("\n"):
                if "PSNR" in line and "average" in line:
                    parts = line.split()
                    for part in parts:
                        if part.startswith("average:"):
                            try:
                                return float(part.split(":")[1])
                            except:
                                continue

            # Si no se encontró PSNR en el output, retornar None
            return None

        except Exception as e:
            print(f"Advertencia: No se pudo calcular PSNR: {e}")
            return None

    def create_qp_size_plots(self, qp_values, size_values, frame_types):
        """Crea gráficas de evolución de QP y SIZE por frame."""
        try:
            import matplotlib

            matplotlib.use("Agg")  # Backend no-GUI
            import matplotlib.pyplot as plt
            from matplotlib.patches import Rectangle
            from matplotlib.ticker import MultipleLocator
        except ImportError:
            print("matplotlib no disponible, omitiendo creación de gráficas")
            return

        print(
            f"Creando gráficas con {len(qp_values)} valores QP, {len(size_values)} valores SIZE, {len(frame_types)} tipos de frame"
        )

        # Crear figura con 2 subplots
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

        frames = list(range(1, len(qp_values) + 1))

        # Gráfica QP
        ax1.plot(
            frames, qp_values, "b-", linewidth=2, marker="o", markersize=2, alpha=0.7
        )
        ax1.set_ylabel("QP Value", fontsize=12)
        ax1.set_title("Evolución de QP", fontsize=14, fontweight="bold")
        ax1.grid(True, alpha=0.3, which="both", linestyle="--", linewidth=0.5)
        ax1.set_xlim(1, len(frames))
        ax1.set_ylim(1, 51)  # Escala fija entre 1 y 51

        # Más valores en eje X para QP
        ax1.xaxis.set_major_locator(
            MultipleLocator(max(1, len(frames) // 20))
        )  # Cada 5% aproximadamente
        ax1.xaxis.set_minor_locator(
            MultipleLocator(max(1, len(frames) // 100))
        )  # Rejilla fina cada 1%

        # Gráfica SIZE con barras coloreadas por tipo de frame
        colors = []
        for i, frame_type in enumerate(
            frame_types[: len(size_values)]
        ):  # Asegurar que no exceda el tamaño
            if frame_type == "I":
                colors.append("red")
            elif frame_type == "P":
                colors.append("blue")
            elif frame_type == "B":
                colors.append("blueviolet")
            else:
                colors.append("gray")  # Para tipos desconocidos

            if i < 5:  # Debug primeros 5
                print(
                    f"Frame {i + 1}: tipo={frame_type}, size={size_values[i]}, color={colors[-1]}"
                )

        print(
            f"Total barras a dibujar: {len(colors)}, valores size: {len(size_values)}"
        )
        print(f"Colores únicos: {set(colors)}")
        print(f"Rango de valores size: {min(size_values)} - {max(size_values)}")

        ax2.bar(frames, size_values, color=colors, alpha=0.7, width=0.8)
        ax2.set_xlabel("Frame Number", fontsize=12)
        ax2.set_ylabel("Size (bytes)", fontsize=12)
        ax2.set_title("Tipos de frames y su tamaño", fontsize=14, fontweight="bold")
        ax2.grid(True, alpha=0.3, which="both", linestyle="--", linewidth=0.5)
        ax2.set_xlim(0.5, len(frames) + 0.5)

        # Más valores en eje X para SIZE
        ax2.xaxis.set_major_locator(
            MultipleLocator(max(1, len(frames) // 20))
        )  # Cada 5% aproximadamente
        ax2.xaxis.set_minor_locator(
            MultipleLocator(max(1, len(frames) // 100))
        )  # Rejilla fina cada 1%

        # Añadir leyenda
        legend_elements = [
            Rectangle((0, 0), 1, 1, facecolor="red", alpha=0.7, label="I-Frame"),
            Rectangle((0, 0), 1, 1, facecolor="blue", alpha=0.7, label="P-Frame"),
            Rectangle((0, 0), 1, 1, facecolor="blueviolet", alpha=0.7, label="B-Frame"),
        ]
        ax2.legend(handles=legend_elements, loc="upper right")

        plt.tight_layout()

        # Guardar gráfica
        root, _ = os.path.splitext(self.output_mp4)
        plot_file = f"{root}_qp_size_plots.png"
        plt.savefig(plot_file, dpi=150, bbox_inches="tight")
        plt.close()

        print(f"[Listo] Gráficas QP/SIZE guardadas: {plot_file}")

    def get_mb_category(self, token):
        """
        Determina el color basado en el caracter del macrobloque.
        Basado en la salida ASCII típica de libx264/ffmpeg debug.
        """
        if isinstance(token, int) or (isinstance(token, str) and token.isdigit()):
            token_value = int(token)
            if token_value == 0:
                return self.colors["SKIP"]
            if token_value == 2:
                return self.colors["INTRA"]
            return self.colors["INTER"]

        t = str(token).upper()

        # Intra: I, i
        if "I" in t:
            return self.colors["INTRA"]

        # Skip: S (en frames P), s
        if "S" in t or any(c in t for c in ["D", "G"]):
            return self.colors["SKIP"]

        # Inter: d, D, <, >, X, etc.
        # d/D: 16x16
        # < >: particiones rectangulares 16x8 / 8x16
        # X: 8x8
        if any(c in t for c in ["D", "X", "<", ">", "+", "-", "|"]):
            return self.colors["INTER"]

        # Para casos no identificados, asumir INTER (más común)
        return self.colors["INTER"]

    def get_mb_type_name(self, token):
        """
        Devuelve el nombre de la categoría del macrobloque como string.
        """
        if isinstance(token, int) or (isinstance(token, str) and token.isdigit()):
            token_value = int(token)
            if token_value == 0:
                return "SKIP"
            if token_value == 2:
                return "INTRA"
            return "INTER"

        t = str(token).upper()

        # Intra: I, i
        if "I" in t:
            return "INTRA"

        # Skip: S (en frames P), s
        if "S" in t or any(c in t for c in ["D", "G"]):
            return "SKIP"

        # Inter: d, D, <, >, X, etc.
        if any(c in t for c in ["D", "X", "<", ">", "+", "-", "|"]):
            return "INTER"

        # Para casos no identificados, asumir INTER (más común)
        return "INTER"

    def print_legend(self):
        """Imprime una leyenda explicativa de los símbolos al final del proceso."""
        print("\n" + "=" * 50)
        print("               LEYENDA DE SÍMBOLOS")
        print("=" * 50)
        print(" [ INTRA (Rojo) ] - Referencia espacial (dentro del mismo frame)")
        print("   I : Intra 16x16 (Bloque grande plano)")
        print("   i : Intra 4x4 / 8x8 (Bloque con detalle)")
        print("-" * 50)
        print(" [ SKIP (Verde) ] - Copiado directo")
        print("   S : Macrobloque Skip (Copiado del frame anterior, muy eficiente)")
        print("-" * 50)
        print(" [ INTER (Azul) ] - Referencia temporal (movimiento)")
        print("   d : Inter 16x16 (Predicción L0)")
        print("   D : Inter 16x16 (Predicción L1 / Bi-pred / Direct)")
        print("   < : Partición 16x8 (Rectangular horizontal)")
        print("   > : Partición 8x16 (Rectangular vertical)")
        print("   X : Partición 8x8 (Sub-particionado)")
        print("=" * 50)

    def _get_video_properties(self):
        if not os.path.exists(self.output_mp4):
            print(f"Error: El video codificado no existe: {self.output_mp4}")
            sys.exit(1)

        file_size = os.path.getsize(self.output_mp4)
        if file_size == 0:
            print(f"Error: El video codificado está vacío: {self.output_mp4}")
            sys.exit(1)

        cap = cv2.VideoCapture(self.output_mp4)
        if not cap.isOpened():
            print(f"Error: No se pudo abrir el video codificado: {self.output_mp4}")
            sys.exit(1)

        properties = {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),  # type: ignore[attr-defined]
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),  # type: ignore[attr-defined]
            "fps": cap.get(cv2.CAP_PROP_FPS),  # type: ignore[attr-defined]
            "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),  # type: ignore[attr-defined]
        }
        cap.release()
        return properties

    def _parse_mb_log_frames(self):
        if not os.path.exists(self.log_file):
            print(f"Error: Archivo de log no encontrado: {self.log_file}")
            sys.exit(1)

        frames_data = []
        current_frame_data = {"type": None, "tokens": []}
        regex_prefix = re.compile(r"^\[.*?\]\s*")

        with open(self.log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                clean = regex_prefix.sub("", line).strip()
                if not clean:
                    continue

                if (
                    "New frame" in clean
                    or "nal_unit_type: 1" in clean
                    and "slice" in clean
                ):
                    if current_frame_data["tokens"]:
                        frames_data.append(current_frame_data)
                        current_frame_data = {"type": None, "tokens": []}

                    if "New frame" in clean:
                        type_match = re.search(r"type:\s*([IPB])", clean, re.IGNORECASE)
                        if type_match:
                            frame_type = type_match.group(1).upper()
                            if frame_type in ["I", "P", "B"]:
                                current_frame_data["type"] = frame_type
                    continue

                if any(
                    x in clean
                    for x in [
                        "nal_unit_type",
                        "Decoding",
                        "Format",
                        "Reinit",
                        "no picture",
                        "Stream",
                        "Metadata",
                        "Duration",
                        "fps=",
                        "frame=",
                    ]
                ):
                    continue

                tokens = clean.split()
                skip_words = [
                    "No",
                    "automatically",
                    "explicit",
                    "mapping",
                    "maps",
                    "streams",
                    "New",
                    "frame",
                    "type",
                    "Decoding",
                    "Format",
                    "Reinit",
                ]

                if (
                    tokens
                    and len(tokens[0]) <= 2
                    and not any(word in clean for word in skip_words)
                    and not clean.startswith("[")
                    and not any(
                        char.isdigit() and len(tokens[0]) > 1 for char in tokens[0]
                    )
                ):
                    current_frame_data["tokens"].extend(tokens)

        if current_frame_data["tokens"]:
            frames_data.append(current_frame_data)

        return frames_data

    def generate_analysis_sidecar(self):
        self.run_step("Generando sidecar de análisis")

        properties = self._get_video_properties()
        width = properties["width"]
        height = properties["height"]
        fps = properties["fps"]
        total_frames_video = properties["total_frames"]

        mb_w, mb_h = 16, 16
        cols = (width + mb_w - 1) // mb_w
        rows = (height + mb_h - 1) // mb_h

        frames_data = self._parse_mb_log_frames()

        self.mb_stats = {
            "total_mb": 0,
            "by_type": {"INTRA": 0, "SKIP": 0, "INTER": 0},
            "by_frame_type": [],
        }

        type_counter = {"I": 0, "P": 0, "B": 0}
        analysis_frames = []
        qp_values = self.frame_data.get("qp_values", [])
        size_values = self.frame_data.get("size_values", [])
        bitrate_values = self.frame_data.get("bitrate_values", [])
        motion_vectors = self.frame_data.get("motion_vectors", [])
        qp_frame_types = self.frame_stats.get("frame_types", [])

        for idx, frame_data in enumerate(frames_data):
            tokens = frame_data.get("tokens", [])
            frame_type = qp_frame_types[idx] if idx < len(qp_frame_types) else None
            if frame_type is None:
                frame_type = frame_data.get("type")
            if frame_type is None and idx < len(qp_frame_types):
                frame_type = qp_frame_types[idx]
            if frame_type in type_counter:
                type_counter[frame_type] += 1

            frame_stats = {"INTRA": 0, "SKIP": 0, "INTER": 0, "total": 0}
            for token in tokens[: rows * cols]:
                mb_type = self.get_mb_type_name(token)
                self.mb_stats["by_type"][mb_type] += 1
                self.mb_stats["total_mb"] += 1
                frame_stats[mb_type] += 1
                frame_stats["total"] += 1

            self.mb_stats["by_frame_type"].append(frame_stats)

            total_mb = frame_stats["total"]
            mb_stats = {
                "total_mb": total_mb,
                "intra_count": frame_stats["INTRA"],
                "skip_count": frame_stats["SKIP"],
                "inter_count": frame_stats["INTER"],
                "intra_pct": (frame_stats["INTRA"] / total_mb * 100) if total_mb else 0,
                "skip_pct": (frame_stats["SKIP"] / total_mb * 100) if total_mb else 0,
                "inter_pct": (frame_stats["INTER"] / total_mb * 100) if total_mb else 0,
            }

            analysis_frames.append(
                {
                    "frame_index": idx,
                    "display_index": idx + 1,
                    "type": frame_type or "U",
                    "qp": qp_values[idx] if idx < len(qp_values) else None,
                    "size": size_values[idx] if idx < len(size_values) else None,
                    "bitrate": bitrate_values[idx]
                    if idx < len(bitrate_values)
                    else None,
                    "motion_vectors": motion_vectors[idx]
                    if idx < len(motion_vectors)
                    else [],
                    "blocks": tokens,
                    "mb_stats": mb_stats,
                }
            )

        self.frame_stats["by_type"] = type_counter
        self.frame_stats["total_frames"] = sum(type_counter.values())

        try:
            input_size = os.path.getsize(self.input_file)
            output_size = os.path.getsize(self.output_mp4)
            compression_ratio = input_size / output_size if output_size > 0 else 0
            compression_percentage = (
                (1 - output_size / input_size) * 100 if input_size > 0 else 0
            )
        except (OSError, FileNotFoundError):
            input_size = output_size = compression_ratio = compression_percentage = 0

        psnr_value = self.calculate_psnr()

        avg_bitrate = None
        if self.frame_data["bitrate_values"]:
            valid_bitrates = [
                b for b in self.frame_data["bitrate_values"] if b is not None
            ]
            if valid_bitrates:
                avg_bitrate = sum(valid_bitrates) / len(valid_bitrates)

        total_frames = self.frame_stats["total_frames"]
        frame_type_percentages = {
            frame_type: ((count / total_frames) * 100 if total_frames else 0)
            for frame_type, count in type_counter.items()
        }

        total_macroblocks = self.mb_stats["total_mb"]
        macroblock_percentages = {
            mb_type: ((count / total_macroblocks) * 100 if total_macroblocks else 0)
            for mb_type, count in self.mb_stats["by_type"].items()
        }

        analysis_payload = {
            "metadata": {
                "format_version": 1,
                "input_file": self.input_file,
                "output_video": self.output_mp4,
                "analysis_file": self.analysis_file,
                "ffmpeg_path": self.ffmpeg_path,
                "ffprobe_path": self.ffprobe_path,
                "ffmpeg_params": self.ffmpeg_params,
                "source_log": self.log_file,
            },
            "video_info": {
                "width": width,
                "height": height,
                "fps": fps,
                "total_frames": total_frames_video,
                "mb_width": mb_w,
                "mb_height": mb_h,
                "mb_cols": cols,
                "mb_rows": rows,
            },
            "summary": {
                "input_size": input_size,
                "output_size": output_size,
                "compression_ratio": compression_ratio,
                "compression_percentage": compression_percentage,
                "avg_bitrate": avg_bitrate,
                "avg_psnr": psnr_value,
                "frame_type_counts": type_counter,
                "frame_type_percentages": frame_type_percentages,
                "macroblock_totals": self.mb_stats["by_type"],
                "macroblock_percentages": macroblock_percentages,
                "total_macroblocks": self.mb_stats["total_mb"],
                "total_motion_vectors": sum(len(v) for v in motion_vectors),
                "parsed_frames": len(analysis_frames),
            },
            "frames": analysis_frames,
        }

        with open(self.analysis_file, "w", encoding="utf-8") as f:
            json.dump(analysis_payload, f, ensure_ascii=False, indent=2)

        print(f"[Listo] Sidecar de análisis generado: {self.analysis_file}")

        if not self.keep_debug_log:
            try:
                if os.path.exists(self.log_file):
                    os.remove(self.log_file)
                    print(f"Archivo de log temporal eliminado: {self.log_file}")
            except OSError as e:
                print(f"Advertencia: No se pudo eliminar el log temporal: {e}")

        return analysis_payload

    def generate_statistics_file(self):
        """Genera un archivo con estadísticas de los tipos de macroblocks."""
        self.run_step("Generando archivo de estadísticas de macroblocks")

        # Usar el mismo nombre que el video de salida pero con sufijo _stats
        root, _ = os.path.splitext(self.output_mp4)
        stats_file = f"{root}_stats.txt"

        with open(stats_file, "w", encoding="utf-8") as f:
            # Calcular estadísticas de compresión
            try:
                input_size = os.path.getsize(self.input_file)
                output_size = os.path.getsize(self.output_mp4)
                compression_ratio = input_size / output_size if output_size > 0 else 0
                compression_percentage = (
                    (1 - output_size / input_size) * 100 if input_size > 0 else 0
                )
            except (OSError, FileNotFoundError):
                input_size = output_size = compression_ratio = (
                    compression_percentage
                ) = 0

            # Calcular PSNR
            psnr_value = self.calculate_psnr()

            # Calcular bitrate promedio
            avg_bitrate = None
            if self.frame_data["bitrate_values"]:
                valid_bitrates = [
                    b for b in self.frame_data["bitrate_values"] if b is not None
                ]
                if valid_bitrates:
                    avg_bitrate = sum(valid_bitrates) / len(valid_bitrates)

            f.write("RESUMEN GENERAL\n")
            f.write("-" * 30 + "\n")
            f.write(f"Total de macroblocks analizados: {self.mb_stats['total_mb']:,}\n")
            f.write(
                f"Total de frames analizados: {len(self.mb_stats['by_frame_type'])}\n"
            )
            if input_size > 0 and output_size > 0:
                f.write(f"Tamaño archivo original: {input_size:,} bytes\n")
                f.write(f"Tamaño archivo comprimido: {output_size:,} bytes\n")
                f.write(f"Ratio de compresión: {compression_ratio:.2f}:1\n")
                f.write(f"Porcentaje de compresión: {compression_percentage:.1f}%\n")
            if avg_bitrate is not None:
                f.write(
                    f"Bitrate promedio: {avg_bitrate:,.0f} bps ({avg_bitrate / 1000:,.0f} kbps)\n"
                )
            if psnr_value is not None:
                f.write(f"PSNR promedio: {psnr_value:.2f} dB\n")
            f.write("\n")

            f.write("DISTRIBUCIÓN POR TIPO DE MACROBLOCK\n")
            f.write("-" * 40 + "\n")
            f.write(
                "Tipo".ljust(10) + "Cantidad".rjust(12) + "Porcentaje".rjust(12) + "\n"
            )
            f.write("-" * 40 + "\n")

            for mb_type in ["INTRA", "SKIP", "INTER"]:
                count = self.mb_stats["by_type"][mb_type]
                if self.mb_stats["total_mb"] > 0:
                    percentage = (count / self.mb_stats["total_mb"]) * 100
                    f.write(f"{mb_type:<10}{count:>12,}{percentage:>11.2f}%\n")
                else:
                    f.write(f"{mb_type:<10}{count:>12,}N/A\n")

            f.write("-" * 40 + "\n")

        print(f"[Listo] Archivo de estadísticas generado: {stats_file}")

        info_file = f"{root}.info"
        qp_values = self.frame_data.get("qp_values", [])
        size_values = self.frame_data.get("size_values", [])
        frame_types = self.frame_stats.get("frame_types", [])

        if qp_values and size_values:
            with open(info_file, "w", encoding="utf-8") as f:
                f.write("# frame,type,qp,size_bytes\n")
                for idx, (qp, size) in enumerate(zip(qp_values, size_values)):
                    frame_type = frame_types[idx] if idx < len(frame_types) else "U"
                    f.write(f"{idx},{frame_type},{qp},{size}\n")

            print(f"[Listo] Archivo de información por frame generado: {info_file}")
