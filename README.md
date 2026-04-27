# H264TT (H264 Teaching Tool)

H264TT is a teaching-oriented H.264 analysis tool built around FFmpeg. It lets you encode video, extract low-level analysis data, and inspect the results through an interactive GUI with macroblock overlays, frame statistics, QP evolution, frame-size plots, and motion vectors.

## Main capabilities

- Interactive GUI for H.264 teaching and inspection
- Macroblock overlay with color-coded INTRA / SKIP / INTER blocks
- Frame-by-frame statistics:
  - frame type
  - average QP
  - encoded frame size
  - macroblock distribution
- Analysis sidecar generation (`.analysis.json`)
- QP and frame-size plots
- Motion-vector extraction and overlay rendering
- CLI mode for batch analysis workflows
- Configurable FFmpeg / FFprobe executable paths

## FFmpeg compatibility

This project is designed around **FFmpeg 6.1.1**.

Later versions may still work, but low-level debug output and teaching-oriented internals are not guaranteed to behave identically. If you want consistent macroblock analysis results, use FFmpeg 6.1.1 whenever possible.

Also note:

- H.264 encoding features require a build with **`libx264` enabled**
- motion-vector extraction requires FFmpeg development libraries if the native helper must be compiled locally

## Requirements

- Python 3.10+
- FFmpeg
- FFprobe

You can use either `uv` or a traditional `pip`-based environment.

## Installation

Clone the repository:

```bash
git clone https://github.com/valentinbarral/H264TT.git
cd H264TT
```

Install dependencies:

```bash
uv sync
```

### Installation without uv

Create and activate a virtual environment if you want:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies with `pip`:

```bash
pip install -r requirements.txt
```

## Running the tool

### With uv

#### GUI

```bash
uv run H264TT
```

#### CLI

```bash
uv run H264TT-cli input_video.mp4 --params "-c:v libx264 -preset medium -crf 23"
```

#### Diagnostic utility

```bash
uv run H264TT-diagnose my_log.txt
```

### Without uv

If you installed the dependencies with `pip`, launch the tools with Python directly:

#### GUI

```bash
python3 H264TT.py
```

#### CLI

```bash
python3 H264TT_cli.py input_video.mp4 --params "-c:v libx264 -preset medium -crf 23"
```

#### Diagnostic utility

```bash
python3 H264TT_diagnose.py my_log.txt
```

## GUI workflow

Typical workflow in the GUI:

1. Select an input video
2. Choose an output video name
3. Configure encoding parameters
4. Start encoding
5. Let H264TT generate:
   - encoded video
   - analysis sidecar
   - statistics file
   - QP / frame-size plots
6. Inspect the result directly in the GUI

The GUI can automatically load the encoded video after a successful run.

## Local settings persistence

When you save FFmpeg / FFprobe paths from the settings dialog, H264TT stores them locally in:

```text
.h264tt_settings.json
```

That file is automatically loaded the next time the application starts in the same working directory.

## CLI usage examples

### Basic CLI usage

```bash
uv run H264TT-cli input_video.mp4
```

### Custom encoding parameters

```bash
uv run H264TT-cli input_video.mp4 --params "-c:v libx264 -preset slow -crf 20"
```

### Custom FFmpeg / FFprobe paths

```bash
uv run H264TT-cli input_video.mp4 \
  --ffmpeg-path /path/to/ffmpeg \
  --ffprobe-path /path/to/ffprobe
```

### Custom output video name

```bash
uv run H264TT-cli input_video.mp4 --output-video my_output.mp4
```

### Convert MP4 to YUV

```bash
uv run H264TT-cli input.mp4 --create-yuv --yuv-width 1920 --yuv-height 1080
```

### Process a raw YUV input

```bash
uv run H264TT-cli input.yuv --params "-s 1920x1080 -r 30 -pix_fmt yuv420p -f rawvideo -c:v libx264 -preset medium"
```

## Output files

Depending on the workflow, H264TT can generate:

- encoded video (`.mp4`)
- analysis sidecar (`.analysis.json`)
- compatibility frame info export (`.info`)
- statistics file (`_stats.txt`)
- QP / frame-size plots (`_qp_size_plots.png`)

## Notes on motion vectors

The GUI supports interactive motion-vector visualization.

Internally, motion vectors are extracted as data and stored in the analysis sidecar, rather than being burned directly into a derived video. This makes the overlay toggleable and compatible with the rest of the analysis UI.

## Troubleshooting

### FFmpeg encoding fails immediately

Check whether your FFmpeg build includes `libx264`:

```bash
ffmpeg -encoders | grep x264
```

If `libx264` is missing, H.264 teaching workflows will not work correctly.

### Qt / OpenCV plugin problems

The project uses `opencv-python-headless` to reduce common GUI conflicts while still providing `cv2`.

### No graphical display available

If you are running remotely or in a headless environment, make sure your Qt display environment is configured correctly before launching the GUI.

## Project structure

Main package:

- `h264tt/`
  - `core/`
  - `gui/`
  - `native/`

Launchers:

- `H264TT.py`
- `H264TT_cli.py`
- `H264TT_diagnose.py`

## License

This work is licensed under the **Creative Commons Attribution 4.0 International License (CC BY 4.0)**.

**Author:** Valentin Barral

More information:

https://creativecommons.org/licenses/by/4.0/
