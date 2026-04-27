import argparse
import sys

from h264tt.core.visualizer import MBVisualizer, create_yuv_from_mp4


def build_parser():
    parser = argparse.ArgumentParser(
        description="H264TT (H264 Teaching Tool) - visualizador de tipos de macrobloques H.264"
    )
    parser.add_argument("input", help="Archivo de video de entrada")
    parser.add_argument(
        "--params",
        default="-c:v libx264 -preset medium -crf 23",
        help="Parámetros de codificación FFmpeg",
    )
    parser.add_argument(
        "--out",
        default="output",
        help="Prefijo de archivos temporales/salida por defecto",
    )
    parser.add_argument(
        "--output-video",
        help="Nombre específico para el video de salida generado (opcional)",
    )
    parser.add_argument(
        "--create-yuv",
        action="store_true",
        help="Crear archivo YUV desde MP4 en lugar de procesar video existente",
    )
    parser.add_argument(
        "--yuv-width",
        type=int,
        default=720,
        help="Ancho para YUV generado (default: 720)",
    )
    parser.add_argument(
        "--yuv-height",
        type=int,
        default=398,
        help="Alto para YUV generado (default: 398)",
    )
    parser.add_argument(
        "--yuv-fps", type=int, default=25, help="FPS para YUV generado (default: 25)"
    )
    parser.add_argument(
        "--ffmpeg-path",
        default="ffmpeg",
        help="Path al ejecutable de FFmpeg (default: ffmpeg)",
    )
    parser.add_argument(
        "--ffprobe-path",
        default="ffprobe",
        help="Path al ejecutable de FFprobe (default: ffprobe)",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.create_yuv:
        if not args.input.lower().endswith((".mp4", ".avi", ".mkv", ".mov")):
            print(
                "❌ Para --create-yuv, el input debe ser un archivo de video (MP4, AVI, etc.)"
            )
            sys.exit(1)

        output_yuv = f"{args.out}_generated.yuv"
        if create_yuv_from_mp4(
            args.input,
            output_yuv,
            args.yuv_width,
            args.yuv_height,
            args.yuv_fps,
            args.ffmpeg_path,
        ):
            print(f"✅ YUV generado: {output_yuv}")
            print("💡 Ahora puedes procesarlo con:")
            print(
                f'   python3 {sys.argv[0]} {output_yuv} --params "-s {args.yuv_width}x{args.yuv_height} -r {args.yuv_fps} -pix_fmt yuv420p -f rawvideo -c:v libx264 -x264-params keyint=60:min-keyint=60"'
            )
        return

    vis = MBVisualizer(
        args.input,
        args.out,
        args.params,
        args.output_video,
        args.ffmpeg_path,
        args.ffprobe_path,
    )
    vis.check_ffmpeg()
    vis.encode_video()
    vis.extract_debug_info()
    vis.generate_analysis_sidecar()
    vis.generate_statistics_file()


if __name__ == "__main__":
    main()
