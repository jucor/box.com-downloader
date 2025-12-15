# Box.com PDF Downloader
# Copyright (C) 2018 lfasmpao
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import atexit
import base64
import os
import re
import signal
import subprocess
import sys
import time
import threading
from queue import Queue, Empty

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from tqdm import tqdm
from webdriver_manager.chrome import ChromeDriverManager


def url_checker(url):
    """
       This checks the url format
       :param url Unified Resource Locator
       :rtype: bool
       :return boolean
    """
    url_check_regex = re.compile(
        r'^(?:http|ftp)s?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)  # url check regex
    if re.match(url_check_regex, url) is not None:
        return "box.com" in url  # really?


class Scraper:
    def __init__(self, url, driver_location=None, wait_time=None):
        """
        This starts a selenium session
        :param url box.com url
        :type url string
        :param driver_location chrome driver path
        :type driver_location string
        """

        chrome_options = Options()
        # Use new headless mode (less detectable than old --headless)
        chrome_options.add_argument("--headless=new")
        # Use wide viewport to get higher resolution previews from Box.com
        chrome_options.add_argument("--window-size=2560,4000")
        # Force high DPI to get sharper images (simulates retina display)
        chrome_options.add_argument("--force-device-scale-factor=2")
        # Anti-detection flags to appear more like a real browser
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        # Enable performance logging for CDP network interception
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        self.wait_load_time = wait_time
        self.driver_location = driver_location
        self.url = url
        if driver_location:
            service = Service(executable_path=driver_location)
        else:
            service = Service(ChromeDriverManager().install())
        self.driver_obj = webdriver.Chrome(service=service, options=chrome_options)

        # Register cleanup handlers to prevent shell crashes
        atexit.register(self._cleanup)
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)

    def _cleanup(self):
        """Ensure ChromeDriver is properly terminated."""
        try:
            if hasattr(self, 'driver_obj') and self.driver_obj:
                self.driver_obj.quit()
                self.driver_obj = None
        except Exception:
            pass
        # Kill any orphaned chromedriver processes
        try:
            subprocess.run(['pkill', '-f', 'chromedriver'],
                          capture_output=True, timeout=5)
        except Exception:
            pass

    def _signal_handler(self, signum, frame):
        """Handle interrupt signals gracefully."""
        print(f"\nReceived signal {signum}, cleaning up...")
        self._cleanup()
        sys.exit(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.clean()
        return False

    def load_url(self):
        """
        This will load the url on the selenium driver.
        Waits for the preview to actually load instead of a fixed time.
        """
        driver = self.driver_obj
        url = self.url
        driver.get(url)  # load selenium

        # Smart wait: poll for preview content instead of fixed sleep
        max_wait = self.wait_load_time
        poll_interval = 0.5
        waited = 0

        print(f"Waiting for preview to load (max {max_wait}s)...")

        while waited < max_wait:
            # Check if preview content has loaded
            ready = driver.execute_script("""
                // Check for data: images (PDF pages rendered)
                var imgs = document.querySelectorAll('img[src^="data:"]');
                for (var i = 0; i < imgs.length; i++) {
                    if (imgs[i].src.length > 1000) return true;
                }

                // Check for canvas elements (alternative rendering)
                var canvases = document.querySelectorAll('canvas');
                for (var i = 0; i < canvases.length; i++) {
                    var rect = canvases[i].getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 100) return true;
                }

                // Check for Box.com preview container with content
                var containers = document.querySelectorAll('[class*="bp-doc"], [class*="PreviewContent"], [class*="preview-content"]');
                for (var i = 0; i < containers.length; i++) {
                    if (containers[i].scrollHeight > 1000) return true;
                }

                return false;
            """)

            if ready:
                print(f"Preview loaded in {waited:.1f}s")
                # Give it a tiny bit more time to fully render
                time.sleep(0.5)
                return

            time.sleep(poll_interval)
            waited += poll_interval

        print(f"Timeout after {max_wait}s, proceeding anyway...")

    def get_download_title(self):
        """
        This parses box.com title
        :rtype: string
        :return download title
        """
        driver = self.driver_obj
        title = str(driver.title).split("|")[0][:-1]
        return str(title.split(".")[:-1][0])  # split and remove extra white space

    def get_download_url(self):
        """
        This parses box.com url into PDF downloadable file
        :rtype string
        :returns box.com download_url else return None
        """
        driver = self.driver_obj  # get driver
        # Load network requests - get only the name property to avoid serialization issues
        network_requests = driver.execute_script(
            "return window.performance.getEntries().map(function(e) { return e.name; });"
        )
        download_url = None  # default or error
        for name in network_requests:
            # this will scrap the pdf file in the word
            if ("internal_files" in name) and ("pdf" in name):  # check for a pdf file
                download_url = name
        return download_url

    def capture_preview_images_cdp(self, output_dir, scroll_pause=0.5, max_pages=None):
        """
        Optimized capture using blob URL conversion.
        Box.com uses blob: URLs which can't be intercepted via network.
        This method converts blob images to data URLs efficiently.
        :param output_dir: Directory to save the images
        :param scroll_pause: Time to wait after each scroll
        :param max_pages: Maximum number of pages to capture (None for unlimited)
        :return: Number of images captured
        """
        driver = self.driver_obj
        os.makedirs(output_dir, exist_ok=True)

        print(f"Starting optimized blob capture... (max: {max_pages or 'unlimited'})")

        # Get estimated page count from scroll height or page indicator
        page_info = driver.execute_script("""
            var result = {total: null, estimated: null};

            // Try to get exact page count from text (e.g., "1 of 500")
            var text = document.body.innerText;
            var match = text.match(/of\\s+(\\d+)/i) || text.match(/(\\d+)\\s+pages?/i);
            if (match) result.total = parseInt(match[1]);

            // Estimate from scroll height and actual page element height
            var selectors = ['.bp-doc', '[class*="bp-doc"]', '.PreviewContent', '[class*="PreviewContent"]'];
            for (var i = 0; i < selectors.length; i++) {
                var els = document.querySelectorAll(selectors[i]);
                for (var j = 0; j < els.length; j++) {
                    var container = els[j];
                    if (container.scrollHeight > 10000) {
                        // Try to find actual page elements to get real page height
                        var pages = container.querySelectorAll('[class*="page"], [class*="Page"], img[src^="blob:"], img[src^="data:"]');
                        if (pages.length > 0) {
                            var firstPage = pages[0];
                            var pageHeight = firstPage.getBoundingClientRect().height;
                            if (pageHeight > 100) {
                                // Add small margin between pages (~10px)
                                result.estimated = Math.floor(container.scrollHeight / (pageHeight + 10));
                                break;
                            }
                        }
                        // Fallback: assume ~1000px per page (typical for PDFs at 72dpi)
                        result.estimated = Math.floor(container.scrollHeight / 1000);
                        break;
                    }
                }
            }
            return result;
        """)

        total_pages = page_info.get('total') or page_info.get('estimated')
        if total_pages:
            print(f"Detected {total_pages} pages")

        # Inject helper to convert blob URLs to data URLs efficiently
        driver.execute_script("""
            window._blobCapture = {
                seenBlobs: new Set(),
                pendingImages: [],

                captureImage: function(img) {
                    return new Promise((resolve) => {
                        if (!img.complete) {
                            img.onload = () => this.doCapture(img, resolve);
                        } else {
                            this.doCapture(img, resolve);
                        }
                    });
                },

                doCapture: function(img, resolve) {
                    try {
                        var canvas = document.createElement('canvas');
                        canvas.width = img.naturalWidth || img.width;
                        canvas.height = img.naturalHeight || img.height;
                        var ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0);
                        resolve(canvas.toDataURL('image/png'));
                    } catch(e) {
                        resolve(null);
                    }
                },

                // Hash image content for deduplication (not URL)
                hashDataUrl: function(dataUrl) {
                    var data = dataUrl.split(',')[1] || '';
                    if (data.length < 1000) return data;
                    // Sample from multiple parts of the image data for content-based hash
                    return data.substring(100, 300) +
                           data.substring(Math.floor(data.length/2), Math.floor(data.length/2) + 200) +
                           data.substring(data.length - 300);
                },

                collectNewBlobs: async function(minWidth, fallbackMinWidth) {
                    var results = [];
                    minWidth = minWidth || 1000;  // Default: prefer high-res images (1024px)
                    fallbackMinWidth = fallbackMinWidth || 850;  // Accept 890px images as fallback

                    // Collect both blob: and data: images
                    var imgs = document.querySelectorAll('img');
                    var highResImages = [];
                    var lowResImages = [];

                    for (var i = 0; i < imgs.length; i++) {
                        var img = imgs[i];
                        var src = img.src;
                        var rect = img.getBoundingClientRect();

                        // Only process visible, reasonably sized images
                        if (rect.width > 50 && rect.height > 50 && img.naturalWidth >= fallbackMinWidth) {
                            var dataUrl = null;
                            if (src.startsWith('blob:')) {
                                dataUrl = await this.captureImage(img);
                            } else if (src.startsWith('data:') && src.length > 1000) {
                                dataUrl = src;
                            }

                            if (dataUrl && dataUrl.length > 1000) {
                                // Use content hash for deduplication, not URL
                                var contentHash = this.hashDataUrl(dataUrl);
                                if (!this.seenBlobs.has(contentHash)) {
                                    var imgData = { dataUrl: dataUrl, width: img.naturalWidth, height: img.naturalHeight, hash: contentHash };
                                    if (img.naturalWidth >= minWidth) {
                                        highResImages.push(imgData);
                                    } else {
                                        lowResImages.push(imgData);
                                    }
                                }
                            }
                        }
                    }

                    // Also capture canvas elements
                    var canvases = document.querySelectorAll('canvas');
                    for (var i = 0; i < canvases.length; i++) {
                        var canvas = canvases[i];
                        var rect = canvas.getBoundingClientRect();
                        if (canvas.width >= fallbackMinWidth && rect.height > 100) {
                            try {
                                var dataUrl = canvas.toDataURL('image/png');
                                if (dataUrl.length > 1000) {
                                    var contentHash = this.hashDataUrl(dataUrl);
                                    if (!this.seenBlobs.has(contentHash)) {
                                        var imgData = { dataUrl: dataUrl, width: canvas.width, height: canvas.height, hash: contentHash };
                                        if (canvas.width >= minWidth) {
                                            highResImages.push(imgData);
                                        } else {
                                            lowResImages.push(imgData);
                                        }
                                    }
                                }
                            } catch(e) {}
                        }
                    }

                    // Prefer high-res, but include low-res if no high-res available
                    // Mark all as seen to avoid re-capturing
                    for (var i = 0; i < highResImages.length; i++) {
                        this.seenBlobs.add(highResImages[i].hash);
                        results.push(highResImages[i]);
                    }
                    // Only add low-res if we haven't captured many high-res this round
                    if (highResImages.length === 0 && lowResImages.length > 0) {
                        for (var i = 0; i < lowResImages.length; i++) {
                            this.seenBlobs.add(lowResImages[i].hash);
                            results.push(lowResImages[i]);
                        }
                    }

                    return results;
                },

                getSeenCount: function() {
                    return this.seenBlobs.size;
                },

                scrollContainer: null,
                lastScrollTop: -1,

                findScrollContainer: function() {
                    if (this.scrollContainer && document.contains(this.scrollContainer)) {
                        return this.scrollContainer;
                    }
                    // Find the actual scrollable container with preview content
                    var selectors = ['.bp-doc', '[class*="bp-doc"]', '.PreviewContent', '[class*="PreviewContent"]', '[class*="bcpr"]', '[class*="preview"]'];
                    for (var i = 0; i < selectors.length; i++) {
                        var els = document.querySelectorAll(selectors[i]);
                        for (var j = 0; j < els.length; j++) {
                            var el = els[j];
                            if (el.scrollHeight > el.clientHeight + 100) {
                                this.scrollContainer = el;
                                return el;
                            }
                        }
                    }
                    // Fallback: find any large scrollable element
                    var all = document.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        var el = all[i];
                        if (el.scrollHeight > 5000 && el.scrollHeight > el.clientHeight + 100) {
                            var style = window.getComputedStyle(el);
                            if (style.overflowY === 'scroll' || style.overflowY === 'auto') {
                                this.scrollContainer = el;
                                return el;
                            }
                        }
                    }
                    return null;
                },

                scroll: function(amount) {
                    var container = this.findScrollContainer();
                    if (container) {
                        var oldTop = container.scrollTop;
                        container.scrollTop += amount || 800;
                        // Check if we actually scrolled
                        if (container.scrollTop === oldTop && oldTop > 0) {
                            // We might be at the end
                            return { scrolled: false, atEnd: true, scrollTop: container.scrollTop, scrollHeight: container.scrollHeight };
                        }
                        this.lastScrollTop = container.scrollTop;
                        return { scrolled: true, atEnd: false, scrollTop: container.scrollTop, scrollHeight: container.scrollHeight };
                    }
                    // Fallback to window scroll
                    var oldY = window.scrollY;
                    window.scrollBy(0, amount || 800);
                    return { scrolled: window.scrollY !== oldY, atEnd: false, scrollTop: window.scrollY, scrollHeight: document.body.scrollHeight };
                },

                getScrollInfo: function() {
                    var container = this.findScrollContainer();
                    if (container) {
                        return {
                            scrollTop: container.scrollTop,
                            scrollHeight: container.scrollHeight,
                            clientHeight: container.clientHeight,
                            atEnd: container.scrollTop + container.clientHeight >= container.scrollHeight - 50
                        };
                    }
                    return {
                        scrollTop: window.scrollY,
                        scrollHeight: document.body.scrollHeight,
                        clientHeight: window.innerHeight,
                        atEnd: window.scrollY + window.innerHeight >= document.body.scrollHeight - 50
                    };
                },

                // Wait for images in viewport to fully load at high resolution
                waitForHighRes: function(targetWidth) {
                    targetWidth = targetWidth || 1000;
                    return new Promise((resolve) => {
                        var checkCount = 0;
                        var lastResolutions = '';
                        var stableCount = 0;
                        var hasHighRes = false;
                        var check = () => {
                            var imgs = document.querySelectorAll('img');
                            var resolutions = [];
                            hasHighRes = false;
                            for (var i = 0; i < imgs.length; i++) {
                                var img = imgs[i];
                                var rect = img.getBoundingClientRect();
                                // Check if image is in viewport
                                if (rect.top < window.innerHeight && rect.bottom > 0 && rect.width > 50) {
                                    resolutions.push(img.naturalWidth + 'x' + img.naturalHeight);
                                    if (img.naturalWidth >= targetWidth) {
                                        hasHighRes = true;
                                    }
                                }
                            }
                            var resStr = resolutions.join(',');
                            // Wait until resolutions stabilize (no changes for 3-5 checks)
                            if (resStr === lastResolutions) {
                                stableCount++;
                            } else {
                                stableCount = 0;
                                lastResolutions = resStr;
                            }
                            checkCount++;
                            // Wait longer if we haven't seen high-res yet (up to 50 checks = 7.5s)
                            var maxChecks = hasHighRes ? 30 : 50;
                            var stableTarget = hasHighRes ? 3 : 5;
                            if (stableCount >= stableTarget || checkCount > maxChecks) {
                                resolve({ hasHighRes: hasHighRes, checkCount: checkCount });
                            } else {
                                setTimeout(check, 150);
                            }
                        };
                        check();
                    });
                },

                // Get info about visible images
                getVisibleImageInfo: function() {
                    var imgs = document.querySelectorAll('img');
                    var info = [];
                    for (var i = 0; i < imgs.length; i++) {
                        var img = imgs[i];
                        var rect = img.getBoundingClientRect();
                        if (rect.width > 50 && rect.height > 50) {
                            info.push({
                                inViewport: rect.top < window.innerHeight && rect.bottom > 0,
                                naturalWidth: img.naturalWidth,
                                naturalHeight: img.naturalHeight,
                                src: img.src.substring(0, 50)
                            });
                        }
                    }
                    return info;
                }
            };
        """)

        pbar_total = max_pages if max_pages else (total_pages or 100)
        pbar = tqdm(total=pbar_total, unit="pages", desc="Capturing", dynamic_ncols=True)

        page_count = 0
        no_new_count = 0
        scroll_count = 0
        max_scrolls = (max_pages or total_pages or 500) * 3  # Allow many scroll attempts

        while no_new_count < 15 and scroll_count < max_scrolls:
            if max_pages and page_count >= max_pages:
                break

            # Collect new blob images (async JS returns a promise)
            new_images = driver.execute_async_script("""
                var callback = arguments[arguments.length - 1];
                window._blobCapture.collectNewBlobs().then(callback);
            """)

            # Save new images
            batch_count = 0
            for img_data in (new_images or []):
                if max_pages and page_count >= max_pages:
                    break

                page_count += 1
                batch_count += 1
                try:
                    data_url = img_data['dataUrl']
                    header, data = data_url.split(',', 1)
                    ext = 'png' if 'png' in header else 'jpg'
                    filename = os.path.join(output_dir, f"page_{page_count:04d}.{ext}")
                    with open(filename, 'wb') as f:
                        f.write(base64.b64decode(data))
                except Exception as e:
                    pbar.write(f"Error saving page {page_count}: {e}")
                    page_count -= 1
                    batch_count -= 1

            # Update progress bar once per batch for accurate rate
            if batch_count > 0:
                pbar.update(batch_count)

            if not new_images:
                no_new_count += 1
            else:
                no_new_count = 0

            # Scroll and check if we've reached the end
            scroll_result = driver.execute_script("return window._blobCapture.scroll(800);")
            scroll_count += 1

            # If we're at the end of the document, stop
            if scroll_result and scroll_result.get('atEnd'):
                scroll_info = driver.execute_script("return window._blobCapture.getScrollInfo();")
                if scroll_info.get('atEnd'):
                    pbar.write(f"Reached end of document at scroll position {scroll_info.get('scrollTop')}/{scroll_info.get('scrollHeight')}")
                    # Do one more collection pass then exit
                    driver.execute_async_script("""
                        var callback = arguments[arguments.length - 1];
                        window._blobCapture.waitForHighRes().then(callback);
                    """)
                    time.sleep(scroll_pause)
                    # Final collection
                    final_images = driver.execute_async_script("""
                        var callback = arguments[arguments.length - 1];
                        window._blobCapture.collectNewBlobs().then(callback);
                    """)
                    for img_data in (final_images or []):
                        if max_pages and page_count >= max_pages:
                            break
                        page_count += 1
                        try:
                            data_url = img_data['dataUrl']
                            header, data = data_url.split(',', 1)
                            ext = 'png' if 'png' in header else 'jpg'
                            filename = os.path.join(output_dir, f"page_{page_count:04d}.{ext}")
                            with open(filename, 'wb') as f:
                                f.write(base64.b64decode(data))
                            pbar.update(1)
                        except Exception as e:
                            pbar.write(f"Error saving page {page_count}: {e}")
                            page_count -= 1
                    break

            # Wait for high-res images to load
            wait_result = driver.execute_async_script("""
                var callback = arguments[arguments.length - 1];
                window._blobCapture.waitForHighRes(1000).then(callback);
            """)
            # If no high-res detected, wait a bit longer
            if wait_result and not wait_result.get('hasHighRes'):
                time.sleep(scroll_pause * 1.5)
            else:
                time.sleep(scroll_pause * 0.5)

        pbar.close()
        print(f"Captured {page_count} pages via blob conversion")
        return page_count

    def capture_preview_images(self, output_dir, scroll_pause=1.5, max_pages=None):
        """
        Scrolls through the PDF preview and captures all data:// images.
        Saves them as numbered image files.
        :param output_dir: Directory to save the images
        :param scroll_pause: Time to wait after each scroll for images to load
        :param max_pages: Maximum number of pages to capture (None for unlimited)
        :return: Number of images captured
        """
        driver = self.driver_obj
        os.makedirs(output_dir, exist_ok=True)

        page_images = []  # Track captured pages
        last_image_count = -1
        no_new_images_count = 0

        print(f"Starting to capture preview images... (max: {max_pages or 'unlimited'})")

        try:
            # Wait for initial load
            time.sleep(3)

            # Try to find the Box.com preview iframe or container
            # First, let's see what we're working with
            container_info = driver.execute_script("""
                // Find scrollable containers
                var scrollables = [];
                var all = document.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    var style = window.getComputedStyle(el);
                    if (style.overflowY === 'scroll' || style.overflowY === 'auto') {
                        if (el.scrollHeight > el.clientHeight) {
                            scrollables.push({
                                tag: el.tagName,
                                class: el.className,
                                id: el.id,
                                scrollHeight: el.scrollHeight,
                                clientHeight: el.clientHeight
                            });
                        }
                    }
                }
                return scrollables;
            """)
            print(f"Found {len(container_info)} scrollable containers")

            # Estimate total pages from scroll height
            estimated_pages = None
            for c in container_info[:5]:
                print(f"  - {c['tag']}.{c['class'][:50] if c['class'] else ''} (scroll: {c['scrollHeight']}, client: {c['clientHeight']})")
                if c['scrollHeight'] > 10000:  # Likely the main container
                    # Estimate ~1000px per page
                    estimated_pages = c['scrollHeight'] // 1000

            # Get total number of pages if available from text
            total_pages = driver.execute_script("""
                // Look for page count indicators
                var text = document.body.innerText;
                var match = text.match(/of\\s+(\\d+)/i) || text.match(/(\\d+)\\s+pages?/i);
                return match ? parseInt(match[1]) : null;
            """)

            if total_pages:
                estimated_pages = total_pages
                print(f"Detected {total_pages} total pages")
            elif estimated_pages:
                print(f"Estimated ~{estimated_pages} pages based on scroll height")

            # Create progress bar - use max_pages if set, otherwise estimated
            pbar_total = max_pages if max_pages else (estimated_pages or 100)
            pbar = tqdm(total=pbar_total, unit="pages", desc="Capturing")

            # Inject a helper script to track images and save them in batches
            # This avoids transferring huge base64 strings for every check
            driver.execute_script("""
                window._boxCapture = {
                    seenHashes: new Set(),
                    newImages: [],

                    getImageHash: function(src) {
                        // Use first 500 chars as hash
                        return src.substring(0, 500);
                    },

                    collectNewImages: function() {
                        var results = [];

                        // Get img elements with data: src
                        var imgs = document.querySelectorAll('img[src^="data:"]');
                        for (var i = 0; i < imgs.length; i++) {
                            var img = imgs[i];
                            var rect = img.getBoundingClientRect();
                            if (img.src.length > 1000 && rect.width > 100 && rect.height > 100) {
                                var hash = this.getImageHash(img.src);
                                if (!this.seenHashes.has(hash)) {
                                    this.seenHashes.add(hash);
                                    results.push({
                                        src: img.src,
                                        width: rect.width,
                                        height: rect.height
                                    });
                                }
                            }
                        }

                        // Also get canvas elements
                        var canvases = document.querySelectorAll('canvas');
                        for (var i = 0; i < canvases.length; i++) {
                            var canvas = canvases[i];
                            var rect = canvas.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 100) {
                                try {
                                    var dataUrl = canvas.toDataURL('image/png');
                                    if (dataUrl.length > 1000) {
                                        var hash = this.getImageHash(dataUrl);
                                        if (!this.seenHashes.has(hash)) {
                                            this.seenHashes.add(hash);
                                            results.push({
                                                src: dataUrl,
                                                width: rect.width,
                                                height: rect.height
                                            });
                                        }
                                    }
                                } catch(e) {}
                            }
                        }

                        return results;
                    },

                    getSeenCount: function() {
                        return this.seenHashes.size;
                    },

                    scrollContainer: null,

                    findScrollContainer: function() {
                        if (this.scrollContainer && document.contains(this.scrollContainer)) {
                            return this.scrollContainer;
                        }
                        var selectors = [
                            '.bp-doc', '[class*="bp-doc"]',
                            '.PreviewContent', '[class*="PreviewContent"]',
                            '[class*="bcpr"]', '[class*="preview"]'
                        ];
                        for (var i = 0; i < selectors.length; i++) {
                            var els = document.querySelectorAll(selectors[i]);
                            for (var j = 0; j < els.length; j++) {
                                var el = els[j];
                                if (el.scrollHeight > el.clientHeight + 100) {
                                    this.scrollContainer = el;
                                    return el;
                                }
                            }
                        }
                        // Fallback: find any large scrollable element
                        var all = document.querySelectorAll('*');
                        for (var i = 0; i < all.length; i++) {
                            var el = all[i];
                            if (el.scrollHeight > 5000 && el.scrollHeight > el.clientHeight + 100) {
                                var style = window.getComputedStyle(el);
                                if (style.overflowY === 'scroll' || style.overflowY === 'auto') {
                                    this.scrollContainer = el;
                                    return el;
                                }
                            }
                        }
                        return null;
                    },

                    scroll: function(amount) {
                        amount = amount || 800;
                        var container = this.findScrollContainer();
                        if (container) {
                            var oldTop = container.scrollTop;
                            container.scrollTop += amount;
                            if (container.scrollTop === oldTop && oldTop > 0) {
                                return { scrolled: false, atEnd: true };
                            }
                            return { scrolled: true, atEnd: false };
                        }
                        var oldY = window.scrollY;
                        window.scrollBy(0, amount);
                        return { scrolled: window.scrollY !== oldY, atEnd: false };
                    },

                    getScrollInfo: function() {
                        var container = this.findScrollContainer();
                        if (container) {
                            return {
                                scrollTop: container.scrollTop,
                                scrollHeight: container.scrollHeight,
                                clientHeight: container.clientHeight,
                                atEnd: container.scrollTop + container.clientHeight >= container.scrollHeight - 50
                            };
                        }
                        return {
                            scrollTop: window.scrollY,
                            scrollHeight: document.body.scrollHeight,
                            clientHeight: window.innerHeight,
                            atEnd: window.scrollY + window.innerHeight >= document.body.scrollHeight - 50
                        };
                    }
                };
            """)

            # Main capture loop - optimized
            scroll_count = 0
            max_scrolls = (max_pages or estimated_pages or 500) * 3

            while no_new_images_count < 15 and scroll_count < max_scrolls:
                # Check if we've reached max pages
                if max_pages and len(page_images) >= max_pages:
                    pbar.write(f"Reached max pages limit ({max_pages})")
                    break

                # Collect only NEW images (deduplication done in browser)
                new_images = driver.execute_script("return window._boxCapture.collectNewImages();")

                # Process and save new images
                for img_data in new_images:
                    page_num = len(page_images) + 1
                    page_images.append(True)  # Just track count

                    # Save the image
                    try:
                        img_src = img_data['src']
                        header, data = img_src.split(',', 1)
                        ext = 'png' if 'png' in header else 'jpg'
                        filename = os.path.join(output_dir, f"page_{page_num:04d}.{ext}")
                        with open(filename, 'wb') as f:
                            f.write(base64.b64decode(data))
                        pbar.update(1)
                        # Update total if we exceed estimate
                        if estimated_pages and page_num > estimated_pages:
                            pbar.total = page_num
                            pbar.refresh()
                    except Exception as e:
                        pbar.write(f"Error saving page {page_num}: {e}")

                    # Check max_pages after each save
                    if max_pages and len(page_images) >= max_pages:
                        break

                current_count = len(page_images)
                if current_count == last_image_count:
                    no_new_images_count += 1
                else:
                    no_new_images_count = 0
                    last_image_count = current_count

                # Scroll and check for end of document
                scroll_result = driver.execute_script("return window._boxCapture.scroll(800);")
                scroll_count += 1

                if scroll_result and scroll_result.get('atEnd'):
                    scroll_info = driver.execute_script("return window._boxCapture.getScrollInfo();")
                    if scroll_info.get('atEnd'):
                        pbar.write(f"Reached end of document")
                        time.sleep(scroll_pause)
                        # Final collection
                        final_images = driver.execute_script("return window._boxCapture.collectNewImages();")
                        for img_data in final_images:
                            if max_pages and len(page_images) >= max_pages:
                                break
                            page_num = len(page_images) + 1
                            page_images.append(True)
                            try:
                                img_src = img_data['src']
                                header, data = img_src.split(',', 1)
                                ext = 'png' if 'png' in header else 'jpg'
                                filename = os.path.join(output_dir, f"page_{page_num:04d}.{ext}")
                                with open(filename, 'wb') as f:
                                    f.write(base64.b64decode(data))
                                pbar.update(1)
                            except Exception as e:
                                pbar.write(f"Error saving page {page_num}: {e}")
                        break

                # Smart wait: check for new images quickly
                wait_start = time.time()
                time.sleep(0.05)  # Minimal wait for scroll to process

                while (time.time() - wait_start) < scroll_pause:
                    new_count = driver.execute_script("return window._boxCapture.getSeenCount();")
                    if new_count > len(page_images):
                        break
                    time.sleep(0.05)

            pbar.close()

        except Exception as e:
            print(f"\nError during capture: {e}")
            import traceback
            traceback.print_exc()

        print(f"Captured {len(page_images)} total pages")
        return len(page_images)

    def clean(self):
        """Close the current selenium session."""
        self._cleanup()
        # Restore original signal handlers
        if hasattr(self, '_original_sigint'):
            signal.signal(signal.SIGINT, self._original_sigint)
        if hasattr(self, '_original_sigterm'):
            signal.signal(signal.SIGTERM, self._original_sigterm)
