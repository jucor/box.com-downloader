#!/usr/bin/env python3
"""Concatenate images into a PDF file."""
import glob
import io
import os

import click
import img2pdf
from PIL import Image


@click.command()
@click.argument('input_dir', type=click.Path(exists=True))
@click.option('--output', '-o', default=None, help='Output PDF path (default: input_dir.pdf)')
@click.option('--pattern', '-p', default='page_*.png', help='Glob pattern for images')
@click.option('--grayscale/--no-grayscale', '-g', default=True, help='Convert to grayscale (default: True)')
def main(input_dir, output, pattern, grayscale):
    """Concatenate images from INPUT_DIR into a single PDF.

    Images are sorted alphabetically by filename.
    """
    # Find all images
    image_files = sorted(glob.glob(os.path.join(input_dir, pattern)))

    # Also try jpg if png pattern didn't find anything
    if not image_files and 'png' in pattern:
        jpg_pattern = pattern.replace('.png', '.jpg')
        image_files = sorted(glob.glob(os.path.join(input_dir, jpg_pattern)))

    if not image_files:
        click.secho(f"No images found matching '{pattern}' in {input_dir}", fg='red')
        raise SystemExit(1)

    click.echo(f"Found {len(image_files)} images")

    # Determine output path
    if output is None:
        output = os.path.basename(input_dir.rstrip('/')) + '.pdf'

    click.echo(f"Creating PDF: {output}" + (" (grayscale)" if grayscale else ""))

    # Convert images to grayscale if requested
    if grayscale:
        image_data = []
        for img_path in image_files:
            img = Image.open(img_path).convert('L')  # Convert to grayscale
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            image_data.append(buf.read())
        with open(output, "wb") as f:
            f.write(img2pdf.convert(image_data))
    else:
        with open(output, "wb") as f:
            f.write(img2pdf.convert(image_files))

    click.secho(f"✓ Created {output} ({len(image_files)} pages)", fg='green')


if __name__ == "__main__":
    main()
