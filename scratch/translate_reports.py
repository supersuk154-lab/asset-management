import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
CLIENTS_DIR = BASE_DIR / "data" / "clients"

replacements = {
    # HTML 및 MD 헤더/레이블 공통
    "Core-Satellite": "핵심-위성",
    "Core 핵심 : Satellite 위성": "핵심 자산 : 위성 자산",
    "Core 핵심 60% : Satellite 위성 40%": "핵심 자산 60% : 위성 자산 40%",
    
    # 괄호 결합 형태
    "(Core, ": "(핵심 자산, ",
    "(Satellite, ": "(위성 자산, ",
    "(Core)": "(핵심 자산)",
    "(Satellite)": "(위성 자산)",
    
    # 텍스트 내 서술 형태
    "Core 자산": "핵심 자산",
    "Satellite 자산": "위성 자산",
    
    # HTML의 그래프 라벨 형태 (숫자 접미사 매칭을 위해 하드코딩 대체 및 유연한 매칭)
    "Core 70%": "핵심(Core) 70%",
    "Satellite 30%": "위성(Satellite) 30%",
    "Satellite 20%": "위성(Satellite) 20%",
    "Satellite 40%": "위성(Satellite) 40%",
    "Satellite 35.0%": "위성(Satellite) 35.0%",
    "Satellite 30.0%": "위성(Satellite) 30.0%",
    "🟣 Satellite": "🟣 위성(Satellite)",
    "stock-type type-sat\">Satellite</span>": "stock-type type-sat\">위성(Satellite)</span>",
    "stock-type type-core\">Core</span>": "stock-type type-core\">핵심(Core)</span>",
}

def translate_file(file_path: Path):
    try:
        content = file_path.read_text(encoding="utf-8")
        original = content
        
        for k, v in replacements.items():
            content = content.replace(k, v)
            
        if content != original:
            file_path.write_text(content, encoding="utf-8")
            print(f"  [변환 완료] {file_path.relative_to(BASE_DIR)}")
    except Exception as e:
        print(f"  [에러] {file_path}: {e}")

def main():
    print("기존 리포트 내 Core/Satellite 용어 한글 변환 시작...")
    if not CLIENTS_DIR.exists():
        print("고객 디렉토리가 존재하지 않습니다.")
        return
        
    for root, dirs, files in os.walk(CLIENTS_DIR):
        for file in files:
            if file.endswith((".md", ".html")):
                translate_file(Path(root) / file)
    print("변환 완료!")

if __name__ == "__main__":
    main()
