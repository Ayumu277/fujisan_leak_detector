#!/usr/bin/env python3
"""
Test script for SerpAPI reverse image search with hash matching
"""

import os
import sys
import requests
from PIL import Image
import io

# Add the backend directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import serpapi_image_search_with_hash_matching

def test_with_sample_image():
    """Test the SerpAPI function with a sample image"""

    # Create a simple test image
    test_image = Image.new('RGB', (100, 100), color='red')
    img_buffer = io.BytesIO()
    test_image.save(img_buffer, format='JPEG')
    image_bytes = img_buffer.getvalue()

    print("🧪 Testing SerpAPI reverse image search with hash matching...")
    print(f"📊 Test image size: {len(image_bytes)} bytes")

    # Execute the search
    result = serpapi_image_search_with_hash_matching(image_bytes)

    print("\n📋 Results:")
    print(f"  Match found: {result['match']}")
    print(f"  Number of URLs: {len(result['urls'])}")

    if result['urls']:
        print("  Matched URLs:")
        for i, url in enumerate(result['urls'], 1):
            print(f"    {i}. {url}")

    return result

def test_with_real_image_url():
    """Test with a real image from the internet"""

    # Use a well-known image that should have matches
    test_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/280px-PNG_transparency_demonstration_1.png"

    try:
        print(f"🌐 Downloading test image from: {test_url}")
        response = requests.get(test_url, timeout=10)
        response.raise_for_status()

        image_bytes = response.content
        print(f"📊 Downloaded image size: {len(image_bytes)} bytes")

        # Execute the search
        result = serpapi_image_search_with_hash_matching(image_bytes)

        print("\n📋 Results:")
        print(f"  Match found: {result['match']}")
        print(f"  Number of URLs: {len(result['urls'])}")

        if result['urls']:
            print("  Matched URLs:")
            for i, url in enumerate(result['urls'], 1):
                print(f"    {i}. {url}")

        return result

    except Exception as e:
        print(f"❌ Error testing with real image: {str(e)}")
        return None

if __name__ == "__main__":
    print("🚀 Starting SerpAPI Hash Matching Tests\n")

    # Test 1: Simple test image
    print("=" * 50)
    print("TEST 1: Simple test image")
    print("=" * 50)
    test_with_sample_image()

    print("\n" + "=" * 50)
    print("TEST 2: Real image from internet")
    print("=" * 50)
    test_with_real_image_url()

    print("\n✅ Tests completed!")