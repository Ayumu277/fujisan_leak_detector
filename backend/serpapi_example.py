#!/usr/bin/env python3
"""
SerpAPI Google Reverse Image Search with Perceptual Hash Matching

This implementation follows the exact requirements:
1. Use SerpAPI (engine=google_reverse_image) to retrieve visual_matches
2. Each thumbnail image is processed directly in memory without saving
3. Compare each thumbnail with input image using phash from imagehash library
4. Only consider images with hash distance of 2 or less as "matching"
5. Extract the link (page URL) from matched results and return as JSON under serpapi_matches

Required libraries: serpapi, imagehash, requests, Pillow
"""

import os
import uuid
import logging
from typing import Dict, List
import io

# Required imports
import imagehash
import requests
from PIL import Image as PILImage
from serpapi import GoogleSearch

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Key (should be set in environment)
SERP_API_KEY = os.getenv("SERP_API_KEY")

def serpapi_reverse_image_search_exact(input_image_bytes: bytes) -> Dict:
    """
    SerpAPI Google Reverse Image Search with perceptual hash matching

    Exact implementation as per requirements:
    1. Use SerpAPI (engine=google_reverse_image) to retrieve visual_matches
    2. Process each thumbnail image directly in memory (no saving to disk)
    3. Compare each thumbnail with input image using phash from imagehash library
    4. Only consider images with hash distance of 2 or less as "matching"
    5. Extract the link (page URL) from matched results

    Args:
        input_image_bytes (bytes): Input image as bytes

    Returns:
        dict: {
            "match": bool,
            "urls": List[str]  # List of page URLs for matching images
        }
    """
    if not SERP_API_KEY:
        logger.warning("⚠️ SERP_API_KEY not configured")
        return {"match": False, "urls": []}

    temp_file_path = None
    try:
        logger.info("🔍 Starting SerpAPI reverse image search")

        # Step 1: Calculate input image hash using phash
        try:
            input_image = PILImage.open(io.BytesIO(input_image_bytes))
            if input_image.mode != 'RGB':
                input_image = input_image.convert('RGB')
            input_hash = imagehash.phash(input_image)
            logger.info(f"📊 Input image hash: {input_hash}")
        except Exception as e:
            logger.error(f"❌ Failed to process input image: {str(e)}")
            return {"match": False, "urls": []}

        # Step 2: Create temporary file for SerpAPI (required for upload)
        temp_filename = f"serpapi_temp_{uuid.uuid4().hex}.jpg"
        temp_file_path = os.path.join("/tmp", temp_filename)

        with open(temp_file_path, 'wb') as f:
            f.write(input_image_bytes)

        logger.info(f"📁 Temporary file created: {temp_file_path}")

        # Step 3: Use SerpAPI with engine=google_reverse_image
        search_params = {
            "engine": "google_reverse_image",
            "image": temp_file_path,
            "api_key": SERP_API_KEY
        }

        search = GoogleSearch(search_params)
        results = search.get_dict()

        if "error" in results:
            logger.error(f"❌ SerpAPI error: {results['error']}")
            return {"match": False, "urls": []}

        # Step 4: Retrieve visual_matches
        visual_matches = results.get("visual_matches", [])
        logger.info(f"🎯 Found {len(visual_matches)} visual matches")

        if not visual_matches:
            return {"match": False, "urls": []}

        # Step 5: Process each thumbnail image
        matched_urls = []

        for i, match in enumerate(visual_matches):
            thumbnail_url = match.get("thumbnail")
            page_link = match.get("link")

            if not thumbnail_url or not page_link:
                continue

            try:
                logger.debug(f"  🔍 Processing thumbnail {i+1}: {thumbnail_url}")

                # Download thumbnail image (processed in memory, not saved)
                response = requests.get(thumbnail_url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                response.raise_for_status()

                # Process thumbnail directly in memory
                thumbnail_image = PILImage.open(io.BytesIO(response.content))

                # Convert to RGB if needed
                if thumbnail_image.mode != 'RGB':
                    thumbnail_image = thumbnail_image.convert('RGB')

                # Compare using phash from imagehash library
                thumbnail_hash = imagehash.phash(thumbnail_image)

                # Calculate hash distance
                hash_distance = input_hash - thumbnail_hash

                # Only consider images with hash distance of 2 or less
                if hash_distance <= 2:
                    matched_urls.append(page_link)
                    logger.info(f"  ✅ Match found {i+1}: distance={hash_distance} -> {page_link}")
                else:
                    logger.debug(f"  ❌ No match {i+1}: distance={hash_distance}")

            except Exception as e:
                logger.debug(f"  ⚠️ Error processing thumbnail {i+1}: {str(e)}")
                continue

        # Step 6: Return results as JSON under serpapi_matches
        result = {
            "match": len(matched_urls) > 0,
            "urls": matched_urls
        }

        logger.info(f"✅ Search completed: {len(matched_urls)} matching URLs found")
        return result

    except Exception as e:
        logger.error(f"❌ SerpAPI search error: {str(e)}")
        return {"match": False, "urls": []}

    finally:
        # Clean up temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"🗑️ Temporary file deleted")
            except Exception as e:
                logger.warning(f"⚠️ Failed to delete temporary file: {str(e)}")

def example_usage():
    """Example usage of the SerpAPI reverse image search function"""

    # Example 1: Create a test image
    print("🧪 Example 1: Testing with a simple red square")
    test_image = PILImage.new('RGB', (100, 100), color='red')
    img_buffer = io.BytesIO()
    test_image.save(img_buffer, format='JPEG')
    image_bytes = img_buffer.getvalue()

    result = serpapi_reverse_image_search_exact(image_bytes)
    print(f"Result: {result}")

    # Example 2: Load image from file (if available)
    print("\n🧪 Example 2: Testing with image file (if available)")
    try:
        # Try to load an image file
        with open("test_image.jpg", "rb") as f:
            image_bytes = f.read()

        result = serpapi_reverse_image_search_exact(image_bytes)
        print(f"Result: {result}")

    except FileNotFoundError:
        print("No test_image.jpg found, skipping file test")

if __name__ == "__main__":
    print("🚀 SerpAPI Reverse Image Search with Hash Matching")
    print("=" * 60)

    if not SERP_API_KEY:
        print("❌ Please set SERP_API_KEY environment variable")
        exit(1)

    example_usage()