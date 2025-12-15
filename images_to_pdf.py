#!/usr/bin/env python3
"""Concatenate images into a PDF file."""
import glob
import os

import click
import img2pdf


@click.command()
@click.argument('input_dir', type=click.Path(exists=True))
@click.option('--output', '-o', default=None, help='Output PDF path (default: input_dir.pdf)')
@click.option('--pattern', '-p', default='page_*.png', help='Glob pattern for images')
def main(input_dir, output, pattern):
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

    click.echo(f"Creating PDF: {output}")

    with open(output, "wb") as f:
        f.write(img2pdf.convert(image_files))

    click.secho(f"✓ Created {output} ({len(image_files)} pages)", fg='green')


if __name__ == "__main__":
    main()
