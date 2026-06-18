import os
import json
import asyncio
import hashlib
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

# OpenAI 기본 설정
openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", "YOUR_LOCAL_API_KEY"))

# ✅ GitHub Secrets 이름과 1:1 매칭
SOURCE_SPREADSHEET_ID = os.environ.get("SOURCE_SPREADSHEET_ID")
TARGET_SPREADSHEET_ID = os.environ.get("TARGET_SPREADSHEET_ID")

# 총 5개 콘셉트 x 3개씩 = 총 15개 타이틀 마스터 컬럼 정의
CONCEPTS = ['A', 'B', 'C', 'D', 'E']
NUMS = [1, 2, 3]
TITLE_COLUMNS = [f"{c}_{n}" for c in CONCEPTS for n in NUMS]  # A_1 ~ E_3 총 15개
BASE_COLUMNS = ["ID", "상품명", "가격", "URL", "이미지URL", "지역", "출발공항"]
COLUMN_ORDER = BASE_COLUMNS + TITLE_COLUMNS

# 동시 호출 제한
LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "15"))


# ==========================================
# [함수 1] 구글 시트 연동 인스턴스 생성
# ==========================================
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_raw = os.environ.get("GOOGLE_JSON_RAW")
    if json_raw:
        return gspread.authorize(Credentials.from_service_account_info(json.loads(json_raw), scopes=scopes))
    return gspread.authorize(Credentials.from_service_account_file('secrets.json', scopes=scopes))


# ==========================================
# [함수 2] 단일 LLM 타이틀 생성기
# ==========================================
async def generate_naver_titles_llm(p, semaphore):
    async with semaphore:
        departure = f"[{p['출발공항']}출발]" if p['출발공항'] != "없음" else ""

        price_grade = (
            "세이브" if "[세이브]" in p['상품명'] else
            "스탠다드" if "[스탠다드]" in p['상품명'] else
            "프리미엄" if "[프리미엄]" in p['상품명'] else
            "일반"
        )

        grade_rule = ""
        if price_grade == "세이브":
            grade_rule = "- 등급 소구: 가성비 실속 라인 플랜입니다. '세이브' 단어는 쓰지 말고 [실속형여행], [가성비추천], [합리적선택], [부담없는플랜] 등의 키워드를 카피마다 다채롭게 흩뿌리세요."
        elif price_grade == "스탠다드":
            grade_rule = "- 등급 소구: 표준 스탠다드 라인입니다. '스탠다드' 단어는 쓰지 말고 [핵심일정포함], [완벽구성패키지], [알찬일정여행], [밸런스추천] 등의 키워드를 흩뿌리세요."
        elif price_grade == "프리미엄":
            grade_rule = "- 등급 소구: 하이엔드 고가 라인입니다. '프리미엄' 단어는 쓰지 말고 [노쇼핑노팁], [풀옵션보장], [여유로운자유시간], [전일정5성급호텔숙박] 등의 고급 키워드를 전면에 배치하세요."

        prompt = f"""
당신은 네이버 쇼핑 검색 최적화(SEO) 및 소비자 클릭률(CTR)을 극대화하는 국내 최고 수준의 퍼포먼스 마케팅 카피라이팅 전문가입니다.
제공된 여행 상품 데이터를 분석하여, 로봇이 공장에서 찍어낸 것 같은 흔적을 완벽히 지우고 실제 베테랑 마케터가 숨을 불어넣은 듯한 차별화된 상품명 15개를 생성하세요.

[입력 상품 데이터]
- 상품 식별 ID: {p['ID']}
- 원본 상품명: {p['상품명']}
- 여행 지역: {p['지역']}
- 가격/금액: {p['가격']:,}원
- 필수 출발지 문구: {departure} (이 문구가 비어있지 않다면 무조건 최종 상품명 가장 맨 앞에 고정 배치할 것)
{grade_rule}

[⚠️ 핵심 개혁: 기계적 단어 돌려막기 절대 금지]
모든 타이틀에 "부모님 효도여행", "아이동반 가족여행", "전일정식사포함", "즐거운여행" 같은 뻔하고 상투적인 문구를 접두사처럼 고정하여 뒤에 단어만 갈아 끼우는 로봇 같은 행위를 전면 금지합니다.
어순을 완전히 파괴하고, 마케팅 소구 단어를 다채롭게 변형하여 15개의 타이틀이 각각 완전히 다른 문장 구조를 가지도록 창조하세요.

[❌ 전 콘셉트 공통 제약 가이드라인]
1. 글자 수 제약: 모든 상품명은 공백 포함 최소 35자 ~ 최대 45자 사이로 풍성하게 구성한다. (45자 절대 초과 금지)
2. ★ 문장부호 사용 제한 및 정제 규칙 ★: 최종 생성되는 모든 상품명 내부에는 쉼표(,), 느낌표(!), 물결(~), 플러스(+) 같은 부호나 특수문자를 절대 포함할 수 없다. 기간이나 범위를 표현할 때 물결 기호(ex: 5~6일)가 필요하다면 무조건 붙임표 대시 기호(ex: 5-6일)로 치환하여 작성해야 한다. 쉼표와 느낌표가 들어갈 자리는 깔끔하게 공백(띄어쓰기) 처리한다.
3. 날것 노출 금지: '#' 기호나 해시태그 형태를 그대로 노출하지 마라. (ex: #디너크루즈 -> 로맨틱디너크루즈투어, #아티타야CC -> 아티타야CC품격라운딩)
4. 정제성: '신상품', '특가', '대박' 같은 유치한 홍보성 접두사나 특수문자는 전면 배제한다. 최종 출력물 텍스트 내부에 "주의:", "경고:", "가이드:" 등 시스템 지시어 성격의 텍스트를 삽입하는 것을 절대 금지한다.
5. 문장 자율성: 기계적인 명사 나열에만 집착하지 말고, 조사와 마케팅 수식어를 자연스럽게 결합하여 소비자가 읽었을 때 매력적인 '명사구' 형태로 늘려라. "행복한여행", "특별한여정" 같이 글자 수 채우기용 무의미한 콤보 수식어는 남발하지 마라.

[🎯 콘셉트별 마케팅 지향점]
■ 콘셉트 A (정석 SEO형 - 3개): 핵심 지역, 일정, 주요 골프장/호텔 명사 위주의 실용적인 변형 조합. (배치 순서를 완전히 섞을 것)
■ 콘셉트 B (타겟/상황형 - 3개): 상투적인 단어 금지. [부모님극찬휴양], [가족취향저격여행], [부부힐링기념], [골프마니아강추] 등 타겟층의 심리를 자극하는 생동감 있는 단어 배치.
■ 콘셉트 C (혜택/USP형 - 3개): 등급에 맞는 핵심 혜택을 명사화하여 소구. (ex: 반나절자유시간확보, 미슐랭맛집투어, 전일정그린피포함 등)
■ 콘셉트 D (감성/트렌디형 - 3개): 요즘뜨는핫플투어, 인생샷명소공략, 감성힐링스팟, 낭만가득일정 등 트렌디한 키워드를 자연스럽게 결합.
■ 콘셉트 E (기본 대안형 - 3개): 원본 상품명이 가진 본연의 가치를 해치지 않는 선에서 네이버 쇼핑 노출 규격(35자~45자)에 맞게 세련되게 다듬은 대안.

반드시 아래 규격의 JSON 오브젝트 포맷으로만 응답하세요. 다른 설명은 전면 금지합니다.
{{
  "A_1": "...", "A_2": "...", "A_3": "...",
  "B_1": "...", "B_2": "...", "B_3": "...",
  "C_1": "...", "C_2": "...", "C_3": "...",
  "D_1": "...", "D_2": "...", "D_3": "...",
  "E_1": "...", "E_2": "...", "E_3": "..."
}}
"""
        json_schema_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "naver_fifteen_titles_single_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {col: {"type": "string"} for col in TITLE_COLUMNS},
                    "required": TITLE_COLUMNS,
                    "additionalProperties": False
                }
            }
        }

        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that outputs compliant JSON based on the provided schema."},
                    {"role": "user", "content": prompt}
                ],
                response_format=json_schema_format,
                temperature=0.75
            )
            return p['ID'], json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"❌ LLM 타이틀 생성 중 에러 발생 (ID: {p['ID']}): {e}")
            return p['ID'], {}


# ==========================================
# [함수 3] 메인 크롤러 및 데이터 파이프라인 엔진
# ==========================================
async def run_pipeline():
    if not SOURCE_SPREADSHEET_ID or not TARGET_SPREADSHEET_ID:
        print("❌ [오류] SOURCE_SPREADSHEET_ID 또는 TARGET_SPREADSHEET_ID 환경 변수가 설정되지 않았습니다.")
        return

    gc = get_gspread_client()
    
    print("📥 [1단계] 타겟 상품리스트 URL 로드 중...")
    try:
        source_doc = gc.open_by_key(SOURCE_SPREADSHEET_ID)
        target_rows = source_doc.worksheet("상품리스트").get_all_values()[1:]
    except Exception as e:
        print(f"❌ 소스 시트 로드 실패 (ID: {SOURCE_SPREADSHEET_ID}): {e}")
        return

    target_tasks = []
    for r in target_rows:
        if r and r[0].startswith("http"):
            raw_airport = r[2].strip() if len(r) > 2 else ""
            airport_val = raw_airport if raw_airport != "" else "없음"
            target_tasks.append({
                "url": r[0].strip(),
                "region": r[1].strip(),
                "airport": airport_val
            })

    print(f"✅ 총 {len(target_tasks)}개의 크롤링 타겟 URL을 확보했습니다.")

    print("\n🕵️ [2단계] 전수 크롤링 및 실시간 스크롤 로딩 시작...")
    crawled_raw_products = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 1024},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        for task in target_tasks:
            MAX_RETRIES = 3
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    print(f"🔄 로딩 중: {task['region']} ({task['airport']}) [시도 {attempt}/{MAX_RETRIES}]")
                    
                    # 💡 대기 기준 변경: 비동기 데이터 렌더링 확보를 위해 networkidle까지 강력 보장
                    await page.goto(task['url'], wait_until="networkidle", timeout=40000)

                    # 💡 안전 장치: 상품 리스트 레이아웃 자체의 렌더링이 보장될 때까지 추가 대기
                    await page.wait_for_selector(".prod_list_wrap ul.type > li", timeout=10000)

                    total_count = 20
                    try:
                        await page.wait_for_selector(".option_wrap.result .count em", timeout=5000)
                        count_el = await page.query_selector(".option_wrap.result .count em")
                        if count_el:
                            total_count = int("".join(filter(str.isdigit, await count_el.inner_text())))
                    except:
                        pass

                    needed_scrolls = (total_count - 1) // 20
                    for s in range(needed_scrolls):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(1.5)  # 💡 물리적 스크롤 후 이미지 렌더링을 유도하는 안전 슬립 지속

                    # 렌더링 완료 후 리스트 파싱 진행
                    items = await page.query_selector_all(".prod_list_wrap ul.type > li")
                    for item in items:
                        main_info = await item.query_selector(".inr.right")
                        img_check = await item.query_selector(".inr.img")
                        if not main_info:
                            continue

                        # 💡 셀렉터 매칭 최적화: 타이틀 확실하게 확보
                        title_el = await main_info.query_selector(".item_title")
                        if not title_el:
                            continue
                        full_title = (await title_el.inner_text()).strip()

                        price_el = await main_info.query_selector(".price")
                        price_raw = await price_el.inner_text() if price_el else "0"
                        price = int("".join(filter(str.isdigit, price_raw))) if any(c.isdigit() for c in price_raw) else 0

                        # 💡 이미지 파싱 로직 간소화 및 정밀화
                        img_url = ""
                        if img_check:
                            img_el = await img_check.query_selector("img")
                            if img_el:
                                # 레이지 로딩 대응: data-src를 먼저 확인 후 없으면 src 확보
                                data_src = await img_el.get_attribute("data-src")
                                src = await img_el.get_attribute("src")
                                potential_url = data_src if data_src else src

                                if potential_url and "bg_alpha" not in potential_url:
                                    img_url = potential_url.strip()

                        if img_url and img_url.startswith("//"):
                            img_url = "https:" + img_url

                        # 로그 확인용 디버깅 메세지
                        if not img_url:
                            print(f"⚠️ 이미지 URL 누락: 상품명={full_title[:15]}")
                        if full_title == "제목 없음":
                            print(f"⚠️ 제목 파싱 실패 케이스 발견 (ID 대조용)")

                        unique_str = f"{full_title}_{price}_{task['airport']}"
                        product_id = hashlib.md5(unique_str.encode('utf-8')).hexdigest()[:8]

                        crawled_raw_products.append({
                            "ID": product_id,
                            "상품명": full_title,
                            "가격": price,
                            "URL": task['url'],
                            "이미지URL": img_url,
                            "지역": task['region'],
                            "출발공항": task['airport']
                        })

                    break

                except Exception as e:
                    print(f"⚠️ [{attempt}/{MAX_RETRIES}] URL 예외 발생 ({task['url']}): {e}")
                    if attempt == MAX_RETRIES:
                        print(f"❌ 최대 재시도 횟수 초과. 해당 URL 최종 스킵: {task['url']}")
                    else:
                        await asyncio.sleep(3)

        await browser.close()

    df_new = pd.DataFrame(crawled_raw_products)
    print(f"✅ 크롤링 전수 완료: 현재 웹상에 살아있는 상품 총 {len(df_new)}개 수집됨.")

    # 3~5. 데이터 대조 연산
    print("\n📊 [3~5단계] 최신화 연산 진행 (중복 제거 및 마스터 정제)...")
    df_final = df_new.drop_duplicates(subset=["ID"]).copy()
    for col in TITLE_COLUMNS:
        df_final[col] = ""

    worksheet_name = "github"

    try:
        target_doc = gc.open_by_key(TARGET_SPREADSHEET_ID)
        old_records = target_doc.worksheet(worksheet_name).get_all_records()
        if old_records:
            df_old = pd.DataFrame(old_records)
            if all(col in df_old.columns for col in ["ID"] + TITLE_COLUMNS):
                df_old_titles = df_old[["ID"] + TITLE_COLUMNS].drop_duplicates(subset=["ID"])
                df_final = pd.merge(
                    df_final.drop(columns=TITLE_COLUMNS, errors='ignore'),
                    df_old_titles,
                    on="ID",
                    how="left"
                )
                for col in TITLE_COLUMNS:
                    df_final[col] = df_final[col].fillna("")
                print("✅ [스마트 증분] 기존 적재된 15대 콘셉트 타이틀 매핑 성공 및 LLM 차단 보전 완료.")
    except Exception as e:
        print(f"ℹ️ 기존 적재 시트 대조 패스 (신규 생성 처리 예정): {e}")

    is_new_product = df_final["A_1"] == ""
    df_need_llm = df_final[is_new_product].copy()

    print(f"🚀 [초고속 1:1 병렬 연산] 총 {len(df_final)}개 상품 중 신규 연산 대상 상품: {len(df_need_llm)}개")

    if len(df_need_llm) > 0:
        records_to_llm = df_need_llm.to_dict(orient="records")
        sem = asyncio.Semaphore(LLM_CONCURRENCY)
        tasks = [generate_naver_titles_llm(p, sem) for p in records_to_llm]

        print(f"🔗 총 {len(tasks)}개의 상품을 각각 개별 독립 프롬프트로 분할하여 OpenAI 서버로 동시 발송합니다...")
        llm_results = await asyncio.gather(*tasks)
        print("📥 모든 독립 연산 응답 수신 완료! 데이터프레임 매핑을 시작합니다.")

        for p_id, res in llm_results:
            if not res:
                continue
            matched = df_final[df_final["ID"] == p_id]
            if matched.empty:
                print(f"⚠️ 매핑 실패: ID '{p_id}'가 df_final에 존재하지 않습니다. 스킵합니다.")
                continue
            idx = matched.index[0]
            for col in TITLE_COLUMNS:
                df_final.at[idx, col] = res.get(col, "[Error]").strip()

    # 6. 최종 데이터 적재
    print(f"\n💾 [6단계] 최종 데이터 적재 준비 (총 {len(df_final)}개 상품)...")
    df_final = df_final.reindex(columns=COLUMN_ORDER, fill_value="")
    data_to_upload = [df_final.columns.values.tolist()] + df_final.values.tolist()

    try:
        target_doc = gc.open_by_key(TARGET_SPREADSHEET_ID)
        sheet = target_doc.worksheet(worksheet_name)
        sheet.clear()
        sheet.update(values=data_to_upload, range_name='A1')
        print(f"🚀 [적재 완료] 타겟 시트 [{target_doc.title}] 동기화 성공!")
    except Exception as e:
        print(f"❌ 시트 적재 실패 (ID: {TARGET_SPREADSHEET_ID}): {e}")

    print(f"\n🎉 고유 ID 기반 마스터 {len(COLUMN_ORDER)}대 컬럼 데이터 최신화 파이프라인이 정상 종료되었습니다!")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
