import json
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader

ROOT=Path(r'C:\Users\wxj_1\Desktop\health-management')
QA=ROOT/'tmp'/'pdfs'/'full_rewrite_qa'
PDF=ROOT/'output'/'pdf'/'Agent开发面试题_全量参考答案_易懂版_按轻记工程逐题重写.pdf'

def main():
    files=sorted(QA.glob('page-*.png'))
    assert len(files)==len(PdfReader(str(PDF)).pages)
    for start in range(0,len(files),16):
        sheet=Image.new('RGB',(1120,1640),'#e5e7eb')
        draw=ImageDraw.Draw(sheet)
        for offset,path in enumerate(files[start:start+16]):
            thumb=Image.open(path).convert('RGB')
            thumb.thumbnail((266,378))
            x=(offset%4)*280+(280-thumb.width)//2
            y=(offset//4)*410+22
            sheet.paste(thumb,(x,y))
            draw.text(((offset%4)*280+9,(offset//4)*410+4),path.stem,fill='black')
        sheet.save(QA/f'contact-{start//16+1:02}.png')
    reader=PdfReader(str(PDF))
    short=[]
    for index,page in enumerate(reader.pages,1):
        text=page.extract_text() or ''
        if len(text)<300:
            short.append((index,len(text),text[:120]))
    print(json.dumps(dict(pages=len(files),contacts=(len(files)+15)//16,short_pages=short),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
