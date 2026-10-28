# High-Quality WebP to Signal Animated Sticker Converter

A specialized Python tool to convert animated `.webp` files into **Signal-compliant APNG stickers** with maximum visual fidelity and strict adherence to Signal's limits.

---

## Signal Sticker Specifications Enforced

- **Format:** APNG (`.png`)
- **Canvas Size:** `512 × 512 px` (aspect ratio preserved, centered on transparent canvas)
- **Max File Size:** `300 KB` (307,200 bytes)
- **Max Animation Duration:** `3.0 seconds`
- **Max Frame Rate:** Capped at `20 fps` for smooth playback and low bandwidth
- **Looping:** Infinite loop (`loop=0`)

---

## Key Quality Improvements

1. **Lossless RGBA First:** Prioritizes 32-bit true-color + 8-bit alpha. If the animation naturally fits under 300 KB, it outputs 100% lossless quality with zero quantization.
2. **Clean Alpha Processing:** Fully zeroes RGB values for transparent pixels ($A=0$), eliminating hidden byte bloat and enabling APNG dirty-rectangle delta compression to work with pixel perfection.
3. **Clean Edge Alpha Compositing:** Eliminates alpha-squaring and color-multiplying artifacts when placing stickers on the 512×512 canvas, preventing dark halos, fringing, and washed-out edges.
4. **Full-Resolution Montage Quantization:** When palette reduction is necessary to hit $<300$ KB, the script quantizes a full-resolution montage of all frames. This preserves crisp outlines, vibrant highlights, and smooth anti-aliased alpha transparency without box-blur degradation.
5. **Conservative Decimation:** Preserves high frame rates whenever possible, only dropping alternating frames if an animation has an exceptionally high FPS ($>12$ fps) and cannot fit under 300 KB otherwise.

---

## Installation & Requirements

Requires Python 3 and Pillow:

```bash
pip install Pillow
```

Make the script executable:
```bash
chmod +x webp_to_signal.py
```

---

## Usage

### 1. Convert a Single File
```bash
# Outputs to sticker.png in the same directory:
./webp_to_signal.py sticker.webp

# Specify custom output path:
./webp_to_signal.py sticker.webp -o ./signal_sticker.png
```

### 2. Convert an Entire Folder
```bash
# Automatically creates folder '<name>_signal_stickers':
./webp_to_signal.py ./my_webp_stickers/

# Specify custom output folder:
./webp_to_signal.py ./my_webp_stickers/ -o ./converted_stickers/
```

---

## Options & Flags

| Flag | Default | Description |
| :--- | :--- | :--- |
| `-o, --output` | *auto* | Destination `.png` file or output folder. |
| `--max-kb` | `300` | Maximum file size in KB (default: 300 KB for Signal). |
| `--max-duration` | `3.0` | Maximum animation duration in seconds. |
| `--duration-mode` | `fit` | `fit` speeds up animations $>3$s; `trim` cuts off frames past 3s. |
| `--max-fps` | `20` | Caps the frame rate to ensure sticker plays smoothly under 300 KB. |
| `--size` | `512` | Canvas dimensions in pixels. |
| `--no-pad` | *off* | Skip transparent padding to 512x512 square. |
| `--no-upscale` | *off* | Do not stretch stickers smaller than 512x512; place them centered. |
| `-v, --verbose` | *off* | Print details of each stage during adaptive compression. |

---

## Uploading to Signal

1. Open **Signal Desktop**.
2. Go to **File** $\to$ **Create/Upload Sticker Pack** (or `Ctrl+Shift+S` / `Cmd+Shift+S`).
3. Drag-and-drop all converted `.png` files into the window.
4. Assign an emoji to each sticker, set pack metadata, and click **Upload**.
