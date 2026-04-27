#!/usr/bin/env python3
"""
Script de diagnóstico para verificar el parseo de macrobloques.
Analiza un archivo de log de FFmpeg y muestra estadísticas de tipos de MB por frame.
"""

import sys
import re


def analyze_mb_log(log_file):
    """Analiza un archivo de log de FFmpeg y muestra estadísticas."""

    print(f"Analizando: {log_file}\n")

    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Buscar frames
    frame_pattern = r"New frame, type: ([IPB])"
    frames = re.finditer(frame_pattern, content)

    current_frame_type = None
    frame_num = 0

    for line in content.split("\n"):
        # Detectar nuevo frame
        frame_match = re.search(frame_pattern, line)
        if frame_match:
            current_frame_type = frame_match.group(1)
            frame_num += 1
            print(f"\n{'=' * 60}")
            print(f"Frame {frame_num}: Tipo {current_frame_type}")
            print(f"{'=' * 60}")
            continue

        # Buscar líneas que parezcan contener símbolos de MB
        if current_frame_type and line.strip():
            # Filtrar líneas de debug que no son MB
            if any(
                char in line
                for char in [":", "=", "[", "]", "(", ")", "{", "}", "@", "#"]
            ):
                continue

            skip_words = [
                "New",
                "frame",
                "type",
                "w:",
                "h:",
                "pixfmt",
                "query_formats",
                "Decoder",
                "EOF",
                "Terminating",
                "Output",
                "stream",
                "Total",
            ]
            if any(word in line for word in skip_words):
                continue

            # Dividir en símbolos
            symbols = line.split()

            # Verificar si parece una línea de MB (muchos símbolos cortos)
            if len(symbols) >= 5:
                valid_symbols = [s for s in symbols if 1 <= len(s.strip()) <= 3]
                if len(valid_symbols) / len(symbols) >= 0.8:
                    # Contar tipos de símbolos
                    symbol_counts = {}
                    for sym in valid_symbols:
                        sym = sym.strip()
                        if sym:
                            first_char = sym[0]
                            symbol_counts[first_char] = (
                                symbol_counts.get(first_char, 0) + 1
                            )

                    # Mostrar solo si hay símbolos interesantes
                    if symbol_counts:
                        print(f"  Símbolos encontrados: {symbol_counts}")

                        # VERIFICACIÓN: Detectar símbolos sospechosos
                        if current_frame_type == "P":
                            if "<" in symbol_counts or "X" in symbol_counts:
                                print(
                                    f"  ⚠️  ADVERTENCIA: Frame P con símbolos de B-frame!"
                                )
                                print(f"      Línea: {line[:100]}")

                        # Mostrar primeros símbolos
                        print(f"  Primeros 20 símbolos: {' '.join(valid_symbols[:20])}")

                        if frame_num >= 5:  # Limitar a primeros 5 frames
                            break


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 H264TT_diagnose.py <archivo.log>")
        print("\nEste script analiza la salida de debug de FFmpeg para verificar")
        print("qué símbolos de macrobloques se están generando por tipo de frame.")
        sys.exit(1)

    analyze_mb_log(sys.argv[1])


if __name__ == "__main__":
    main()
