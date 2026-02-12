import asyncio
import os
import sys

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.services.excel_batch import excel_batch_service

async def test_json_parsing():
    json_path = r'd:\mycode\P115-Share\frontend\public\static\template\result.json'
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found")
        return

    with open(json_path, 'rb') as f:
        content = f.read()

    print(f"Testing parsing of {json_path}...")
    try:
        # Test _parse_telegram_json directly
        result = excel_batch_service._parse_telegram_json(content)
        print(f"Successfully extracted {len(result)} items.")
        
        # Show first 3 items with metadata
        for i, item in enumerate(result[:3]):
            print(f"Item {i+1}:")
            print(f"  Title: {item['标题']}")
            print(f"  Link: {item['链接']}")
            print(f"  Code: {item['提取码']}")
            if 'item_metadata' in item:
                print(f"  Metadata Text Snippet: {item['item_metadata']['full_text'][:50]}...")
                print(f"  Entities Count: {len(item['item_metadata']['entities'])}")
            
        # Test parse_file (which uses _parse_telegram_json internally)
        parse_res = await excel_batch_service.parse_file(content, 'test.json')
        print("\nParse File Result:")
        print(f"  Headers: {parse_res['headers']}")
        print(f"  Total Rows: {parse_res['total_rows']}")
        print(f"  Preview Count: {len(parse_res['preview'])}")
        
    except Exception as e:
        print(f"Error during parsing: {e}")

if __name__ == "__main__":
    asyncio.run(test_json_parsing())
