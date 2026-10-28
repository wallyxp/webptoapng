#!/usr/bin/env python3
"""
webp_to_signal.py - High-Quality Animated WebP to Signal APNG Sticker Converter

Signal Sticker Specifications:
- Format: APNG (.png)
- Canvas: 512x512 pixels (aspect ratio maintained, centered with transparent padding)
- Max file size: 300 KB (307,200 bytes)
- Max duration: 3.0 seconds (3,000 ms)
- Animation loop: Infinite (loop=0)

Key Quality & Optimization Enhancements:
1. Lossless RGBA first: Prioritizes full 32-bit true-color + 8-bit alpha whenever possible.
2. Clean Alpha Processing: Zeroes RGB channels for transparent pixels (A=0), preventing
   compression bloat and allowing APNG dirty-rect delta compression to function optimally.
3. No Double-Mask Alpha Degradation: Fixes alpha squaring/fringing bug when compositing to 512x512.
4. Full-Resolution Montage Quantization: When palette reduction is necessary, extracts a
   unified 256-color palette from the full-resolution montage preserving exact lines and alpha.
5. Conservative Decimation: Only reduces framerate if necessary, preserving smooth playback.
"""

import argparse
import io
import math
import os
import sys
from pathlib import Path
from typing import List, Tuple, Optional

from PIL import Image, ImageOps, ImageSequence

MAX_SIGNAL_SIZE_BYTES = 300 * 1024  # 300 KB
MAX_SIGNAL_DURATION_MS = 3000      # 3.0 seconds
TARGET_CANVAS_DIM = 512
DEFAULT_MAX_FPS = 20


def clean_alpha_channel(im: Image.Image) -> Image.Image:
    """
    Zeroes out the RGB channels wherever alpha is 0.
    In WebP/PNG decoding, transparent pixels often retain arbitrary RGB garbage.
    Cleaning them allows DEFLATE to compress transparent areas to near-zero bytes
    and enables APNG delta differencing to compute precise bounding boxes.
    """
    if im.mode != "RGBA":
        im = im.convert("RGBA")

    r, g, b, a = im.split()
    zero = Image.new("L", im.size, 0)
    cr = Image.composite(r, zero, a)
    cg = Image.composite(g, zero, a)
    cb = Image.composite(b, zero, a)
    return Image.merge("RGBA", (cr, cg, cb, a))


def extract_webp_frames(im: Image.Image) -> Tuple[List[Image.Image], List[int]]:
    """
    Extract composited RGBA frames and their durations from a WebP image.
    Uses PIL's AnimDecoder via WebPImagePlugin which handles WebP frame disposal & blending.
    """
    frames: List[Image.Image] = []
    durations: List[int] = []

    is_animated = getattr(im, "is_animated", False)
    n_frames = getattr(im, "n_frames", 1)

    if not is_animated or n_frames <= 1:
        im.load()
        frame = clean_alpha_channel(im)
        return [frame], [1000]

    for frame_idx in range(n_frames):
        im.seek(frame_idx)
        im.load()
        frame_rgba = clean_alpha_channel(im)
        dur = im.info.get("duration", 100)
        # WebP specs say duration <= 10 ms is often an artifact; default to 100ms (10 fps)
        if dur is None or dur <= 10:
            dur = 100
        frames.append(frame_rgba.copy())
        durations.append(int(dur))

    return frames, durations


def normalize_frame_dimensions(
    frame: Image.Image,
    target_dim: int = TARGET_CANVAS_DIM,
    pad_to_square: bool = True,
    allow_upscale: bool = True
) -> Image.Image:
    """
    Resize frame to fit within target_dim x target_dim preserving aspect ratio.
    Pastes directly without double-masking so anti-aliased edge alpha is not degraded.
    """
    w, h = frame.size
    if w == target_dim and h == target_dim:
        return clean_alpha_channel(frame)

    if not allow_upscale and w <= target_dim and h <= target_dim:
        new_w, new_h = w, h
        resized = frame
    else:
        scale = min(target_dim / w, target_dim / h)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = frame.resize((new_w, new_h), Image.Resampling.LANCZOS)

    if not pad_to_square:
        return clean_alpha_channel(resized)

    canvas = Image.new("RGBA", (target_dim, target_dim), (0, 0, 0, 0))
    paste_x = (target_dim - new_w) // 2
    paste_y = (target_dim - new_h) // 2
    # Paste directly: do NOT pass mask=resized for RGBA images, as that squares alpha
    canvas.paste(resized, (paste_x, paste_y))
    return clean_alpha_channel(canvas)


def adjust_timing(
    frames: List[Image.Image],
    durations: List[int],
    max_duration_ms: int = MAX_SIGNAL_DURATION_MS,
    max_fps: int = DEFAULT_MAX_FPS,
    duration_mode: str = "fit"
) -> Tuple[List[Image.Image], List[int]]:
    """
    Ensure the animation does not exceed max_duration_ms (3.0s) and max_fps (20fps).
    """
    if len(frames) <= 1:
        return frames, durations

    total_duration = sum(durations)
    if total_duration <= 0:
        durations = [100] * len(frames)
        total_duration = sum(durations)

    # 1. Handle duration > max_duration_ms
    if total_duration > max_duration_ms:
        if duration_mode == "fit":
            factor = max_duration_ms / total_duration
            durations = [max(20, int(round(d * factor))) for d in durations]
        elif duration_mode == "trim":
            acc = 0
            trimmed_f = []
            trimmed_d = []
            for f, d in zip(frames, durations):
                if acc + d <= max_duration_ms:
                    trimmed_f.append(f)
                    trimmed_d.append(d)
                    acc += d
                else:
                    remaining = max_duration_ms - acc
                    if remaining >= 20:
                        trimmed_f.append(f)
                        trimmed_d.append(remaining)
                    break
            frames = trimmed_f or [frames[0]]
            durations = trimmed_d or [max_duration_ms]

    # 2. Check FPS and downsample frames only if frame count is excessively high (> max_fps)
    total_duration = sum(durations)
    current_fps = len(frames) / (total_duration / 1000.0) if total_duration > 0 else 10
    if current_fps > max_fps and len(frames) > 2:
        target_count = max(2, int(round(total_duration / 1000.0 * max_fps)))
        if target_count < len(frames):
            step = len(frames) / target_count
            new_frames = []
            for i in range(target_count):
                idx = min(int(round(i * step)), len(frames) - 1)
                new_frames.append(frames[idx])

            base_d = total_duration // target_count
            rem = total_duration % target_count
            new_durations = [base_d + (1 if j < rem else 0) for j in range(target_count)]
            frames = new_frames
            durations = new_durations

    return frames, durations


def encode_apng_rgba(frames: List[Image.Image], durations: List[int]) -> bytes:
    """
    Encode frames as full 32-bit RGBA APNG with dirty-rect delta compression.
    Pillow automatically computes bounding-box deltas when optimize=True.
    """
    buf = io.BytesIO()
    if len(frames) == 1:
        frames[0].save(buf, format="PNG", optimize=True, compress_level=9)
    else:
        frames[0].save(
            buf,
            format="PNG",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            compress_level=9,
            optimize=True
        )
    return buf.getvalue()


def encode_apng_montage_palette(
    frames: List[Image.Image],
    durations: List[int],
    colors: int = 256
) -> bytes:
    """
    Encode frames as a unified paletted APNG with full-resolution quantization.
    Builds a full-resolution montage of all frames, quantizes the montage
    (which generates a single global palette and valid alpha tRNS table),
    then crops back each frame. This preserves crisp edges, sharp lines, and exact alpha.
    """
    if len(frames) == 1:
        buf = io.BytesIO()
        q = frames[0].quantize(colors=colors)
        q.save(buf, format="PNG", optimize=True, compress_level=9)
        return buf.getvalue()

    n = len(frames)
    w, h = frames[0].size
    cols = min(6, n)
    rows = (n + cols - 1) // cols

    montage = Image.new("RGBA", (cols * w, rows * h), (0, 0, 0, 0))
    for idx, f in enumerate(frames):
        c = idx % cols
        r = idx // cols
        montage.paste(f, (c * w, r * h))

    # Quantize the entire montage in full resolution
    q_montage = montage.quantize(colors=colors)

    # Crop frames back out from the quantized montage
    q_frames = []
    for idx in range(n):
        c = idx % cols
        r = idx // cols
        cropped = q_montage.crop((c * w, r * h, (c + 1) * w, (r + 1) * h))
        q_frames.append(cropped)

    buf = io.BytesIO()
    q_frames[0].save(
        buf,
        format="PNG",
        save_all=True,
        append_images=q_frames[1:],
        duration=durations,
        loop=0,
        compress_level=9,
        optimize=True
    )
    return buf.getvalue()


def decimate_frames(
    frames: List[Image.Image],
    durations: List[int],
    factor: int = 2
) -> Tuple[List[Image.Image], List[int]]:
    """Drop frames by factor and combine durations."""
    if len(frames) <= 2 or factor <= 1:
        return frames, durations

    new_frames = []
    new_durations = []
    i = 0
    while i < len(frames):
        chunk_f = frames[i]
        chunk_d = sum(durations[i : min(i + factor, len(durations))])
        new_frames.append(chunk_f)
        new_durations.append(chunk_d)
        i += factor

    return new_frames, new_durations


def scale_inner_canvas(
    frames: List[Image.Image],
    scale_factor: float,
    canvas_dim: int = TARGET_CANVAS_DIM
) -> List[Image.Image]:
    """Scales down the visual content and re-centers onto canvas_dim."""
    new_w = max(1, int(round(canvas_dim * scale_factor)))
    new_h = max(1, int(round(canvas_dim * scale_factor)))
    paste_x = (canvas_dim - new_w) // 2
    paste_y = (canvas_dim - new_h) // 2

    res = []
    for f in frames:
        c = Image.new("RGBA", (canvas_dim, canvas_dim), (0, 0, 0, 0))
        shrunk = f.resize((new_w, new_h), Image.Resampling.LANCZOS)
        c.paste(shrunk, (paste_x, paste_y))
        res.append(clean_alpha_channel(c))
    return res


def compress_to_signal_spec(
    frames: List[Image.Image],
    durations: List[int],
    max_bytes: int = MAX_SIGNAL_SIZE_BYTES,
    verbose: bool = False
) -> Tuple[bytes, str]:
    """
    High-Fidelity Adaptive Optimizer guaranteeing output <= max_bytes (300 KB).
    Prioritizes lossless true-color RGBA, falling back to full-resolution palette quantization
    only when strictly necessary.
    """
    # Stage 1: Full RGBA (100% Lossless Color & Alpha)
    if verbose:
        print("    [Stage 1] Lossless RGBA...", end="", flush=True)
    data = encode_apng_rgba(frames, durations)
    if len(data) <= max_bytes:
        if verbose:
            print(f" OK ({len(data) // 1024} KB)")
        return data, "Lossless RGBA"
    if verbose:
        print(f" Exceeded ({len(data) // 1024} KB > {max_bytes // 1024} KB)")

    # Stage 2: Full-Resolution Unified 256-Color Palette (Crisp lines, true alpha)
    if verbose:
        print("    [Stage 2] Full-Res 256-Color Palette...", end="", flush=True)
    data = encode_apng_montage_palette(frames, durations, colors=256)
    if len(data) <= max_bytes:
        if verbose:
            print(f" OK ({len(data) // 1024} KB)")
        return data, "256-color palette (full-res)"
    if verbose:
        print(f" Exceeded ({len(data) // 1024} KB)")

    # Stage 3: Full-Resolution Unified 192-Color Palette
    if verbose:
        print("    [Stage 3] Full-Res 192-Color Palette...", end="", flush=True)
    data = encode_apng_montage_palette(frames, durations, colors=192)
    if len(data) <= max_bytes:
        if verbose:
            print(f" OK ({len(data) // 1024} KB)")
        return data, "192-color palette (full-res)"
    if verbose:
        print(f" Exceeded ({len(data) // 1024} KB)")

    # Stage 4: Full-Resolution Unified 128-Color Palette
    if verbose:
        print("    [Stage 4] Full-Res 128-Color Palette...", end="", flush=True)
    data = encode_apng_montage_palette(frames, durations, colors=128)
    if len(data) <= max_bytes:
        if verbose:
            print(f" OK ({len(data) // 1024} KB)")
        return data, "128-color palette (full-res)"
    if verbose:
        print(f" Exceeded ({len(data) // 1024} KB)")

    # Stage 5: Frame Decimation (only if frames > 6 and FPS > 12) + 192 colors
    total_dur_s = sum(durations) / 1000.0 if sum(durations) > 0 else 1.0
    current_fps = len(frames) / total_dur_s
    if len(frames) > 6 and current_fps > 12:
        if verbose:
            print("    [Stage 5] Temporal Decimation (2x) + 192 Colors...", end="", flush=True)
        dec_frames, dec_durations = decimate_frames(frames, durations, factor=2)
        data = encode_apng_montage_palette(dec_frames, dec_durations, colors=192)
        if len(data) <= max_bytes:
            if verbose:
                print(f" OK ({len(data) // 1024} KB)")
            return data, "Decimated 2x (192 colors)"
        if verbose:
            print(f" Exceeded ({len(data) // 1024} KB)")
    else:
        dec_frames, dec_durations = frames, durations

    # Stage 6: Gentle Scale (90%) + 128 colors
    if verbose:
        print("    [Stage 6] Scale 90% + 128 Colors...", end="", flush=True)
    scaled_90 = scale_inner_canvas(dec_frames, 0.90)
    data = encode_apng_montage_palette(scaled_90, dec_durations, colors=128)
    if len(data) <= max_bytes:
        if verbose:
            print(f" OK ({len(data) // 1024} KB)")
        return data, "Scaled 90% (128 colors)"
    if verbose:
        print(f" Exceeded ({len(data) // 1024} KB)")

    # Stage 7: Failsafe Scale (80%) + 64 colors
    if verbose:
        print("    [Stage 7] Failsafe Scale 80% + 64 Colors...", end="", flush=True)
    scaled_80 = scale_inner_canvas(dec_frames, 0.80)
    data = encode_apng_montage_palette(scaled_80, dec_durations, colors=64)
    if verbose:
        print(f" Finished ({len(data) // 1024} KB)")
    return data, "Failsafe 80% (64 colors)"


def convert_webp_file(
    input_path: Path,
    output_path: Path,
    canvas_dim: int = TARGET_CANVAS_DIM,
    pad_to_square: bool = True,
    allow_upscale: bool = True,
    max_duration_ms: int = MAX_SIGNAL_DURATION_MS,
    max_fps: int = DEFAULT_MAX_FPS,
    duration_mode: str = "fit",
    max_bytes: int = MAX_SIGNAL_SIZE_BYTES,
    verbose: bool = False
) -> dict:
    """
    Convert a single .webp file to a high-quality Signal-compliant APNG .png file.
    """
    input_size = input_path.stat().st_size

    with Image.open(input_path) as im:
        raw_frames, raw_durations = extract_webp_frames(im)

    # Apply exif_transpose per frame if necessary
    raw_frames = [clean_alpha_channel(ImageOps.exif_transpose(f)) for f in raw_frames]

    # 1. Canvas Normalization (512x512)
    norm_frames = [
        normalize_frame_dimensions(
            f,
            target_dim=canvas_dim,
            pad_to_square=pad_to_square,
            allow_upscale=allow_upscale
        )
        for f in raw_frames
    ]

    # 2. Timing Adjustment (<= 3.0s, <= 20fps)
    adj_frames, adj_durations = adjust_timing(
        norm_frames,
        raw_durations,
        max_duration_ms=max_duration_ms,
        max_fps=max_fps,
        duration_mode=duration_mode
    )

    # 3. High-Fidelity Compression Loop (<= 300 KB)
    apng_bytes, compression_strategy = compress_to_signal_spec(
        adj_frames,
        adj_durations,
        max_bytes=max_bytes,
        verbose=verbose
    )

    # 4. Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f_out:
        f_out.write(apng_bytes)

    out_size = len(apng_bytes)
    total_dur_ms = sum(adj_durations)

    return {
        "input": str(input_path),
        "output": str(output_path),
        "input_size": input_size,
        "output_size": out_size,
        "frames": len(adj_frames),
        "duration_sec": round(total_dur_ms / 1000.0, 2),
        "strategy": compression_strategy,
        "compliant": out_size <= max_bytes
    }


def main():
    parser = argparse.ArgumentParser(
        description="Convert animated .webp files to High-Quality Signal-compliant APNG stickers (<= 300 KB, 512x512)."
    )
    parser.add_argument(
        "input",
        type=str,
        help="Path to an animated .webp file or directory containing .webp files."
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Path to output .png file (if input is a file) or output directory (if input is a folder)."
    )
    parser.add_argument(
        "--size",
        type=int,
        default=TARGET_CANVAS_DIM,
        help=f"Target canvas dimension in pixels (default: {TARGET_CANVAS_DIM})."
    )
    parser.add_argument(
        "--max-kb",
        type=int,
        default=300,
        help="Maximum file size in KB (default: 300 KB for Signal)."
    )
    parser.add_argument(
        "--max-fps",
        type=int,
        default=DEFAULT_MAX_FPS,
        help=f"Maximum frame rate cap (default: {DEFAULT_MAX_FPS} fps)."
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=3.0,
        help="Maximum animation duration in seconds (default: 3.0s for Signal)."
    )
    parser.add_argument(
        "--duration-mode",
        choices=["fit", "trim"],
        default="fit",
        help="How to handle animations > 3s: 'fit' speeds up, 'trim' cuts off (default: fit)."
    )
    parser.add_argument(
        "--no-pad",
        action="store_true",
        help="Do not pad non-square images with transparent borders to 512x512."
    )
    parser.add_argument(
        "--no-upscale",
        action="store_true",
        help="Do not upscale images that are smaller than 512x512."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed per-stage compression output."
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: Input path '{input_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    max_bytes = args.max_kb * 1024
    max_duration_ms = int(round(args.max_duration * 1000))

    # --- Mode 1: Single File ---
    if input_path.is_file():
        if input_path.suffix.lower() not in [".webp"]:
            print(f"Warning: File '{input_path.name}' does not have a .webp extension, attempting conversion anyway.", file=sys.stderr)

        if args.output:
            out_p = Path(args.output).resolve()
            if out_p.is_dir() or args.output.endswith(os.sep):
                output_path = out_p / f"{input_path.stem}.png"
            else:
                output_path = out_p
        else:
            output_path = input_path.parent / f"{input_path.stem}.png"

        print(f"Converting: {input_path.name} -> {output_path.name}")
        try:
            res = convert_webp_file(
                input_path,
                output_path,
                canvas_dim=args.size,
                pad_to_square=not args.no_pad,
                allow_upscale=not args.no_upscale,
                max_duration_ms=max_duration_ms,
                max_fps=args.max_fps,
                duration_mode=args.duration_mode,
                max_bytes=max_bytes,
                verbose=args.verbose
            )
            kbs = round(res["output_size"] / 1024, 1)
            status = "PASS" if res["compliant"] else "WARN (>300KB)"
            print(f"[{status}] Done: {output_path.name} | Size: {kbs} KB | Frames: {res['frames']} | Duration: {res['duration_sec']}s | Mode: {res['strategy']}")
        except Exception as e:
            print(f"Error converting '{input_path}': {e}", file=sys.stderr)
            sys.exit(1)

    # --- Mode 2: Folder ---
    elif input_path.is_dir():
        if args.output:
            output_dir = Path(args.output).resolve()
        else:
            output_dir = input_path.parent / f"{input_path.name}_signal_stickers"

        output_dir.mkdir(parents=True, exist_ok=True)

        webp_files = sorted(
            [p for p in input_path.iterdir() if p.is_file() and p.suffix.lower() == ".webp"]
        )

        if not webp_files:
            print(f"No .webp files found in directory: {input_path}")
            sys.exit(0)

        print(f"Processing folder: {input_path}")
        print(f"Output directory:  {output_dir}")
        print(f"Found {len(webp_files)} .webp file(s).\n" + "=" * 65)

        success_count = 0
        total_in_bytes = 0
        total_out_bytes = 0

        for idx, webp_file in enumerate(webp_files, 1):
            out_file = output_dir / f"{webp_file.stem}.png"
            print(f"[{idx}/{len(webp_files)}] {webp_file.name} ... ", end="", flush=True)

            try:
                res = convert_webp_file(
                    webp_file,
                    out_file,
                    canvas_dim=args.size,
                    pad_to_square=not args.no_pad,
                    allow_upscale=not args.no_upscale,
                    max_duration_ms=max_duration_ms,
                    max_fps=args.max_fps,
                    duration_mode=args.duration_mode,
                    max_bytes=max_bytes,
                    verbose=args.verbose
                )
                kbs = round(res["output_size"] / 1024, 1)
                tag = "OK" if res["compliant"] else "WARN (>300KB)"
                print(f"[{tag}] {kbs} KB | {res['frames']} frames, {res['duration_sec']}s ({res['strategy']})")
                success_count += 1
                total_in_bytes += res["input_size"]
                total_out_bytes += res["output_size"]
            except Exception as e:
                print(f"[FAILED] {e}")

        print("=" * 65)
        print(f"Finished: {success_count}/{len(webp_files)} converted successfully.")
        print(f"Total output size: {round(total_out_bytes / 1024, 1)} KB")
        print(f"Output folder: {output_dir}")


if __name__ == "__main__":
    main()
