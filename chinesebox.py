#!/usr/bin/env python3
import sys
import json
import re
import os
import requests
import time
from urllib.parse import unquote, quote
from datetime import datetime

# Proxy server configuration
PROXY_SERVER = "https://c.map987.dpdns.org/"

def format_size(bytes_size):
    """Format file size for display"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def extract_folder_id_and_size(html_content):
    """
    Extract folder ID and file size information from HTML content
    Returns: (folder_id, total_size) tuple
    """
    # Find lines containing postStreamData
    lines = [line for line in html_content.split('\n') if 'postStreamData' in line]
    if not lines:
        print("Error: postStreamData content not found")
        return None, None

    print("Found key line (first 200 characters):\n", lines[0][:200], "...")
    print("This line in the HTML is in JSON format, inside a script tag ↑:")

    # Extract folder object
    folder_match = re.search(r',"folder":({[^}]*})', lines[0])
    if not folder_match:
        print("Error: Unable to extract folder object")
        return None, None

    # Extract ID
    id_match = re.search(r'"id":([^,]+)', folder_match.group(1))
    if not id_match:
        print("Error: Unable to extract ID from folder object")
        return None, None
    folder_id = id_match.group(1).strip('"\' ')

    # Extract total file size (sum of itemSize from items array)
    size_match = re.search(r'"items":\[({.*?})\]', lines[0], re.DOTALL)
    total_size = 0
    if size_match:
        # Extract all itemSize fields and sum them
        size_matches = re.finditer(r'"itemSize":(\d+)', size_match.group(1))
        total_size = sum(int(m.group(1)) for m in size_matches)

    return folder_id, total_size

def download_with_progress(url, file_path, total_size=0, proxy=False):
    """Download function with single-line refreshing progress display"""
    start_time = time.time()
    downloaded = 0

    # Set proxy URL
    final_url = f"{PROXY_SERVER}{quote(url, safe='')}" if proxy else url

    try:
        response = requests.get(final_url, stream=True, timeout=60)
        response.raise_for_status()

        # If total_size not provided, try to get it from headers
        if total_size <= 0:
            total_size = int(response.headers.get('content-length', 0))

        file_size_str = format_size(total_size) if total_size > 0 else "Unknown size"

        # Initialize progress display
        sys.stdout.write("\rDownload progress: 0.0% | 0.00 B/{} | Speed: 0.00 B/s".format(file_size_str))
        sys.stdout.flush()

        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:  # Filter keep-alive chunks
                    f.write(chunk)
                    downloaded += len(chunk)

                    # Calculate progress
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    percent = downloaded / total_size * 100 if total_size > 0 else 0

                    # Update progress display
                    sys.stdout.write(
                        "\rDownload progress: {:6.1f}% | {}/{} | Speed: {}/s".format(
                            percent,
                            format_size(downloaded),
                            file_size_str,
                            format_size(speed)
                        )
                    )
                    sys.stdout.flush()

        # Clear progress line after download completes, show completion info
        sys.stdout.write("\r" + " " * 80 + "\r")
        print(f"Download complete: {format_size(downloaded)} in {time.time()-start_time:.1f} seconds")
        return True

    except Exception as e:
        sys.stdout.write("\r" + " " * 80 + "\r")
        print(f"Download failed: {str(e)}")
        return False

def download_box_file(url, target_dir="."):
    """Main download function with full proxy support"""
    html_file = None
    json_file = None

    try:
        # Ensure target directory exists
        os.makedirs(target_dir, exist_ok=True)

        # Get share ID
        share_id = url.split('/')[-1]
        html_file = os.path.join(target_dir, f"{share_id}.html")

        # Download HTML page via proxy
        proxy_url = f"{PROXY_SERVER}{quote(url, safe='')}"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Downloading page via proxy: {proxy_url}")

        if not download_with_progress(url, html_file, proxy=True):
            raise ValueError("Page download failed")

        # Extract folder ID and file size
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

        folder_id, total_size = extract_folder_id_and_size(html_content)
        if not folder_id:
            raise ValueError("Unable to extract folder ID")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Successfully obtained folder ID: {folder_id}")
        if total_size > 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Estimated total file size: {format_size(total_size)}")

        # Get download info via proxy
        api_path = (
            f"https://app.box.com/index.php?"
            f"folder_id={folder_id}&"
            f"q%5Bshared_item%5D%5Bshared_name%5D={share_id}&"
            "rm=box_v2_zip_shared_folder"
        )
        proxy_api_url = f"{PROXY_SERVER}{quote(api_path, safe='')}"

        json_file = os.path.join(target_dir, f"{folder_id}.json")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching download info: {proxy_api_url}")

        response = requests.get(proxy_api_url, timeout=30)
        response.raise_for_status()
        json_data = response.json()

        print("\n============ Download info JSON with download link obtained:")
        print(json.dumps(json_data, indent=2))

        download_url = json_data.get('download_url')
        if not download_url:
            raise ValueError("No download_url field in API response")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Got download link: {download_url[:50]}...")

        # Extract file name
        file_name_match = re.search(r'ZipFileName=([^&]+)', download_url)
        if file_name_match:
            file_name = unquote(file_name_match.group(1))
        else:
            file_name = f"box_download_{folder_id}.zip"

        output_path = os.path.join(target_dir, file_name)

        # Download the final file
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting file download: {file_name}")

        """
        Note: The first two requests, including:
        (1) Downloading the HTML of the shared drive link,
        (2) After getting the folder ID from HTML, using
            https://app.box.com/index.php?folder_id={folder_id}&q%5Bshared_item%5D%5Bshared_name%5D={share_id}&rm=box_v2_zip_shared_folder
            to get the final download link,
        Both of the above box.com URLs cannot be accessed directly.
        ===========

        The request below ↓ is for the final download link https://dl.boxcloud.com/...
        This can be accessed directly but download speed is very slow.
        """

        # Use download function with progress display
        if not download_with_progress(download_url, output_path, total_size=total_size):
            raise ValueError("File download failed")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Download complete: {output_path}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Network request failed: {str(e)}")
    except json.JSONDecodeError as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] JSON parsing failed: {str(e)}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error occurred: {str(e)}")
    finally:
        # Clean up temporary files
        for temp_file in [f for f in [html_file, json_file] if f and os.path.exists(f)]:
            try:
                os.remove(temp_file)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Cleaned up temporary file: {temp_file}")
            except:
                pass

    return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python box_downloader.py <box_url> [target_directory]")
        print("Example: python box_downloader.py https://app.box.com/s/abc123 ./downloads")
        sys.exit(1)

    box_url = sys.argv[1]
    target_dir = sys.argv[2] if len(sys.argv) > 2 else "."

    if not download_box_file(box_url, target_dir):
        sys.exit(1)
