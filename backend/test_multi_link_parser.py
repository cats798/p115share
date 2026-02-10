import re

def test_link_extraction():
    link_pattern = r'https?://(?:115\.com|115cdn\.com|anxia\.com)/s/[a-zA-Z0-9]+(?:\?password=[a-zA-Z0-9]+)?'
    
    test_cases = [
        {
            "name": "Single link",
            "text": "Check this: https://115.com/s/swz16sb36gr?password=9527",
            "expected": ["https://115.com/s/swz16sb36gr?password=9527"]
        },
        {
            "name": "Multiple links",
            "text": """
            https://115cdn.com/s/swz16sb36gr?password=9527
            https://115cdn.com/s/swz163o36gr?password=9527
            https://115cdn.com/s/swz163g36gr?password=9527
            """,
            "expected": [
                "https://115cdn.com/s/swz16sb36gr?password=9527",
                "https://115cdn.com/s/swz163o36gr?password=9527",
                "https://115cdn.com/s/swz163g36gr?password=9527"
            ]
        },
        {
            "name": "Duplicates",
            "text": "Link 1: https://115.com/s/123\nLink 1 again: https://115.com/s/123",
            "expected": ["https://115.com/s/123"]
        },
        {
            "name": "Mixed domains",
            "text": "115: https://115.com/s/abc\nCDN: https://115cdn.com/s/def\nAnxia: https://anxia.com/s/ghi",
            "expected": [
                "https://115.com/s/abc",
                "https://115cdn.com/s/def",
                "https://anxia.com/s/ghi"
            ]
        }
    ]

    for case in test_cases:
        found = re.findall(link_pattern, case["text"])
        # Deduplicate while preserving order
        share_urls = []
        seen = set()
        for url in found:
            if url not in seen:
                share_urls.append(url)
                seen.add(url)
        
        assert share_urls == case["expected"], f"Failed {case['name']}: Expected {case['expected']}, got {share_urls}"
        print(f"✅ PASSED: {case['name']}")

if __name__ == "__main__":
    test_link_extraction()
