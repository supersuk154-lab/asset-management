import zipfile
import xml.etree.ElementTree as ET
import os

def docx_to_text(docx_path):
    try:
        # docx is a zip file
        with zipfile.ZipFile(docx_path) as z:
            xml_content = z.read('word/document.xml')
            
        root = ET.fromstring(xml_content)
        
        paragraphs = []
        # Find all paragraph elements
        for paragraph in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
            texts = []
            # Find all text elements within the paragraph
            for text_elem in paragraph.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
                if text_elem.text:
                    texts.append(text_elem.text)
            paragraphs.append(''.join(texts))
            
        return '\n'.join(paragraphs)
    except Exception as e:
        return f"Error: {str(e)}"

docx_path = r"g:\내 드라이브\자산관리 자동화\PB_FP 헷지 전략 자산 규모 및 설계.docx"
text = docx_to_text(docx_path)

output_path = r"g:\내 드라이브\자산관리 자동화\scratch\extracted_doc.txt"
os.makedirs(os.path.dirname(output_path), exist_ok=True)
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(text)

print(f"Extracted text saved to: {output_path}")
print(f"Length of text: {len(text)} characters")
