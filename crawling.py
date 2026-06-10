import os
import json
import asyncio
import hashlib
import re
import time
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from openai import AsyncOpenAI, RateLimitError, APIError, APIConnectionError

# ============================================================
# 설정값 (필요 시 환경변수로 오버라이드 가능)
# ============================================================
LLM_CONCURRENCY   = int(os.environ.get("LLM_CONCURRENCY",   "5"))   # 동시 LLM 요청 수
LLM_MAX_RETRIES   = int(os.environ.get("LLM_MAX_RETRIES",   "3"))
SHEET_CHUNK_SIZE  = int(os.environ.get("SHEET_CHUNK_SIZE",  "500"))  # 구글시트 청크 업로드
SCROLL_SLEEP      = float(os.environ.get("SCROLL_SLEEP",    "1.2"))
CHECKPOINT_FILE   = "checkpoint_scraped.json"                        # 크롤링 중간저장 경로

openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


# ============================================================
# ID 생성: 원본상품명 + 가격 + 출발공항  →  동일상품이라도 출발지 다르면 다른 ID
# ============================================================
def make_product_id(full_title: str, price: str | int, departure_airport: str) -> str:
    """
    [핵심 수정] 출발공항을 ID 구성 요소에 포함.
    - 상품명+가격이 같아도 출발공항이 다르면 별개 상품으로 구분됨.
    - 출발공항이 '없음'인 경우도 그대로 포함하여 일관성 유지.
    """
    unique_str = f"{full_title.strip()}_{price}_{departure_airport.strip()}"
    return hashlib.md5(unique_str.encode()).hexdigest()[:8]


# ============================================================
# LLM: 콘셉트별 상품명 12개 생성
# ============================================================
async def generate_naver_titles_llm(data: dict, semaphore: asyncio.Semaphore) -> tuple:
    """
    [개선]
    - asyncio.Semaphore 로 동시 호출 수 제어 (rate limit 방어)
    - RateLimitError / APIError 구분 재시도
    - 콘셉트 A~D 정의를 프롬프트에 명시
    """
    if data["departure_airport"] != "없음":
        departure_context = (
            f"- 지정 출발공항: {data['departure_airport']} "
            f"(반드시 상품명 맨 앞에 '{data['departure_airport']}' 형식으로 고정 배치)"
        )
    else:
        departure_context = (
            "- 지정 출발공항: 없음 "
            "(★주의: '[기본출발]' 등 출발 관련 문구 절대 금지. 곧바로 지역명부터 시작)"
        )

    prompt = f"""
당신은 네이버 쇼핑 SEO 및 소비자 심리를 꿰뚫는 초일류 퍼포먼스 마케팅 카피라이팅 전문가입니다.
제공된 여행 상품 데이터를 바탕으로, 아래 4가지 콘셉트별로 각 3개씩 총 12개의 상품명을 생성하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[콘셉트 정의 — 각 콘셉트는 완전히 다른 소구 방향]

A_정석: 검색 최적화 정석형
  → 여행지·기간·구성 키워드를 충실히 담아 검색 노출 극대화.
  → 예) 지역명 + 기간 + 핵심 일정/명소 + 구성 키워드 조합

B_타겟: 특정 고객층 타겟형
  → 가족여행·신혼·시니어·혼자여행 등 구체적 대상과 상황을 전면에 내세움.
  → 예) "엄마랑 딸이랑", "신혼부부 추천", "60대 편안한"

C_혜택: 혜택·가격 강조형
  → 포함 내역·무료 혜택·비용 절감 포인트를 직접 강조.
  → 예) "노쇼핑 노팁", "항공+호텔 포함", "전일정 5성급"

D_감성: 감성·분위기 스토리형
  → 여행지의 무드·감성·경험을 시적으로 표현하여 클릭 욕구 자극.
  → 예) "석양 물드는", "한 번쯤 꿈꾸던", "설레는 첫 유럽"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[등급별 고유 수식어 반영 규칙]
원본 상품명의 등급 괄호를 파악하여 모든 콘셉트(A~D)에 아래 수식어를 반영:
- [세이브]  → 실속/알뜰/합리적 가격/가성비 계열 수식어 포함
- [스탠다드] → 알찬구성/핵심일정/밸런스/베스트셀러 계열 수식어 포함
- [프리미엄] → 노쇼핑/노팁/노옵션/5성급/자유시간 계열 수식어 포함

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[입력 데이터]
- 원본 상품명: {data['full_title']}
- 여행 지역:   {data['region']}
- 기간:        {data['duration']}
{departure_context}
- 핵심 설명:   {data['description']}
- 추출 키워드: {data['hashtags']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[절대 금지 공통 가이드라인]
1. 글자 수: 공백 포함 최소 30자 ~ 최대 45자 (50자 절대 초과 금지)
2. 단일 상품명 내 동일 단어 2회 이상 중복 나열 금지
3. '신상품', '세이브', '특가', '대박', '★' 등 홍보성 문구·특수문자 금지
4. 최종 12개 결과물 간 단어 조합·핵심 카피가 겹치지 않도록 완벽히 차별화
5. 출발공항 '없음'이면 반드시 지역명·브랜드명으로 시작
"""

    json_schema_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "naver_twelve_titles_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    key: {"type": "string"}
                    for key in [
                        "A_1","A_2","A_3",
                        "B_1","B_2","B_3",
                        "C_1","C_2","C_3",
                        "D_1","D_2","D_3",
                    ]
                },
                "required": [
                    "A_1","A_2","A_3",
                    "B_1","B_2","B_3",
                    "C_1","C_2","C_3",
                    "D_1","D_2","D_3",
                ],
                "additionalProperties": False,
            },
        },
    }

    current_temp = 0.4
    titles_list: list[str] = []

    async with semaphore:
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                response = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a helpful assistant that outputs compliant JSON based on the provided schema.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format=json_schema_format,
                    temperature=current_temp,
                    seed=42 if attempt == 1 else None,
                )

                res_json = json.loads(response.choices[0].message.content)
                titles_list = [
                    res_json.get(f"{c}_{i}", "").strip()
                    for c in ["A", "B", "C", "D"]
                    for i in [1, 2, 3]
                ]

                # 12개 모두 고유한지 검사
                if len(set(titles_list)) == 12:
                    return tuple(titles_list)

                print(
                    f"  ⚠️ [재시도 {attempt}] 중복 상품명 발생 "
                    f"({12 - len(set(titles_list))}개 중복) — temperature 올려서 재시도"
                )
                current_temp = min(current_temp + 0.15, 1.0)

            except RateLimitError:
                wait = 60 * attempt  # 지수 백오프
                print(f"  🚦 [RateLimit] {wait}초 대기 후 재시도 ({attempt}/{LLM_MAX_RETRIES})")
                await asyncio.sleep(wait)

            except (APIError, APIConnectionError) as e:
                print(f"  ❌ [APIError] {e} — 재시도 {attempt}/{LLM_MAX_RETRIES}")
                if attempt == LLM_MAX_RETRIES:
                    break
                await asyncio.sleep(5 * attempt)

            except Exception as e:
                print(f"  ❌ [LLM 예외] {e}")
                if attempt == LLM_MAX_RETRIES:
                    break

    # 최대 재시도 초과 시 에러 플레이스홀더 반환
    err_t = f"[LLM오류] {data['full_title'][:15]}"
    if len(titles_list) < 12:
        titles_list = [err_t] * 12
    return tuple(titles_list)


# ============================================================
# 크롤러: 단일 상품 엘리먼트 파싱
# ============================================================
async def scrape_single_product_elements(
    item, target_region: str, target_airport: str, current_url: str
) -> dict | None:
    try:
        main_info = await item.query_selector(":scope > .inr.right")
        img_check  = await item.query_selector(":scope > .inr.img")
        if not main_info or not img_check:
            return None

        # 제목
        title_el   = await main_info.query_selector(".item_title")
        full_title = (await title_el.inner_text()).strip() if title_el else "제목 없음"

        # 가격
        price_el  = await main_info.query_selector(".price")
        price_raw = await price_el.inner_text() if price_el else "0"
        price     = "".join(filter(str.isdigit, price_raw))

        # [핵심 수정] 출발공항 포함 ID 생성
        product_id = make_product_id(full_title, price, target_airport)

        # 정제 상품명 + 해시태그 분리
        pure_title_body = re.sub(r"\[.*?\]", "", full_title).strip()
        if "#" in pure_title_body:
            parts          = pure_title_body.split("#")
            pure_title     = parts[0].strip()
            title_hashtags = sorted([p.strip() for p in parts[1:] if p.strip()])
        else:
            pure_title     = pure_title_body
            title_hashtags = []

        hash_span_els = await main_info.query_selector_all(".hash_group span")
        ui_hashtags   = [(await h.inner_text()).replace("#", "").strip() for h in hash_span_els]
        all_hashtags  = sorted(set(title_hashtags + ui_hashtags))

        # 설명
        desc_el      = await main_info.query_selector(".item_text.stit")
        product_desc = (await desc_el.inner_text()).strip() if desc_el else ""

        # 기간
        duration_el  = await main_info.query_selector("span.icn.cal")
        duration_raw = (await duration_el.inner_text()).strip() if duration_el else ""
        duration     = duration_raw.replace("여행기간", "").strip()

        # 이미지 URL
        img_url = ""
        img_el  = await img_check.query_selector("img")
        if img_el:
            data_src = await img_el.get_attribute("data-src")
            src      = await img_el.get_attribute("src")
            candidate = data_src if data_src else src
            if candidate and "bg_alpha" not in candidate:
                img_url = candidate.strip()
            else:
                for im in await img_check.query_selector_all("img"):
                    i_src  = await im.get_attribute("src")
                    i_data = await im.get_attribute("data-src")
                    target = i_data if i_data else i_src
                    if target and "bg_alpha" not in target:
                        img_url = target.strip()
                        break

        if img_url.startswith("//"):
            img_url = "https:" + img_url

        return {
            "ID":        product_id,
            "원본상품명": full_title,
            "정제상품명": pure_title,
            "가격":      int(price) if price else 0,
            "URL":       current_url,
            "이미지URL":  img_url,
            "지정지역":   target_region,
            "출발공항":   target_airport,
            "duration":   duration,
            "description": product_desc,
            "hashtags":   ", ".join(all_hashtags),
        }

    except Exception as e:
        print(f"  ⚠️ 개별 상품 파싱 실패: {e}")
        return None


# ============================================================
# 구글 시트 청크 업로드 (API 한도 방어)
# ============================================================
def upload_to_sheet_in_chunks(sheet, data_rows: list[list]):
    """
    [개선] 한 번에 대량 업로드 시 Google Sheets API 5MB 한도 초과 방어.
    헤더(첫 행)는 clear() 후 update()로, 나머지는 append_rows() 청크 분할.
    """
    if not data_rows:
        return

    header = data_rows[:1]
    body   = data_rows[1:]

    sheet.clear()
    sheet.update(values=header, range_name="A1")

    for i in range(0, len(body), SHEET_CHUNK_SIZE):
        chunk = body[i : i + SHEET_CHUNK_SIZE]
        sheet.append_rows(chunk, value_input_option="USER_ENTERED")
        print(f"  📤 업로드 진행: {min(i + SHEET_CHUNK_SIZE, len(body))}/{len(body)} 행")
        time.sleep(0.5)  # 쓰기 quota 방어


# ============================================================
# 체크포인트 저장 / 불러오기
# ============================================================
def save_checkpoint(data: list[dict]):
    try:
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  💾 체크포인트 저장 완료: {len(data)}개 상품 → {CHECKPOINT_FILE}")
    except Exception as e:
        print(f"  ⚠️ 체크포인트 저장 실패 (무시): {e}")


def load_checkpoint() -> list[dict] | None:
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"  ♻️  체크포인트 복원: {len(data)}개 상품 불러옴 → 크롤링 스킵")
        return data
    except Exception as e:
        print(f"  ⚠️ 체크포인트 로드 실패 (재크롤링): {e}")
        return None


# ============================================================
# 메인
# ============================================================
async def run_crawler():
    # ----------------------------------------------------------
    # 구글 API 인증
    # ----------------------------------------------------------
    print("🌐 구글 API 인증 및 스프레드시트 연결 중...")
    scopes   = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_raw = os.environ.get("GOOGLE_JSON_RAW")

    try:
        if json_raw:
            creds = Credentials.from_service_account_info(json.loads(json_raw), scopes=scopes)
        else:
            creds = Credentials.from_service_account_file("secrets.json", scopes=scopes)
        gc = gspread.authorize(creds)
    except Exception as e:
        print(f"❌ 구글 API 인증 실패: {e}")
        return

    # ----------------------------------------------------------
    # SOURCE: 대상 URL 리스트 로드
    # ----------------------------------------------------------
    source_spreadsheet_id = os.environ.get("SOURCE_SPREADSHEET_ID")
    try:
        source_sheet = gc.open_by_key(source_spreadsheet_id).worksheet("상품리스트")
        all_rows     = source_sheet.get_all_values()
        target_tasks = [
            {
                "url":          row[0].strip(),
                "sheet_region": row[1].strip() if len(row) > 1 and row[1].strip() else "지역명 미상",
                "sheet_airport": row[2].strip() if len(row) > 2 and row[2].strip() else "없음",
            }
            for row in all_rows[1:]
            if len(row) >= 1 and row[0].startswith("http")
        ]
        print(f"✅ 총 {len(target_tasks)}개의 대상 URL 확보.")
    except Exception as e:
        print(f"❌ URL 리스트 로드 실패: {e}")
        return

    # ----------------------------------------------------------
    # TARGET: 기존 마스터 캐시 로드
    # ----------------------------------------------------------
    target_spreadsheet_id = os.environ.get("TARGET_SPREADSHEET_ID")
    existing_titles_dict: dict[str, list] = {}

    try:
        github_sheet  = gc.open_by_key(target_spreadsheet_id).worksheet("github")
        existing_data = github_sheet.get_all_records()
        for r in existing_data:
            if r.get("ID"):
                existing_titles_dict[str(r["ID"])] = [
                    r.get("A_정석_1",""), r.get("A_정석_2",""), r.get("A_정석_3",""),
                    r.get("B_타겟_1",""), r.get("B_타겟_2",""), r.get("B_타겟_3",""),
                    r.get("C_혜택_1",""), r.get("C_혜택_2",""), r.get("C_혜택_3",""),
                    r.get("D_감성_1",""), r.get("D_감성_2",""), r.get("D_감성_3",""),
                ]
        print(f"✅ 기존 마스터 데이터 {len(existing_titles_dict)}개 캐싱 완료.")
    except Exception as e:
        print(f"⚠️ 기존 시트 로드 패스 (신규 시트로 간주): {e}")
        github_sheet = None

    # ----------------------------------------------------------
    # STAGE 1: 웹 크롤링 (체크포인트가 있으면 스킵)
    # ----------------------------------------------------------
    raw_scraped_list = load_checkpoint()

    if raw_scraped_list is None:
        print("\n⚡ [STAGE 1] 전체 기획전 URL 대상 웹 스크래핑 시작...")
        raw_scraped_list = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 1024},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            for idx, task in enumerate(target_tasks, start=1):
                current_url    = task["url"]
                target_region  = task["sheet_region"]
                target_airport = task["sheet_airport"]

                try:
                    print(f"🔄 [{idx}/{len(target_tasks)}] {target_region} ({target_airport}) 스크래핑 중...")
                    await page.goto(current_url, wait_until="domcontentloaded", timeout=25000)

                    try:
                        await page.wait_for_selector(".option_wrap.result .count em", timeout=5000)
                    except Exception:
                        pass

                    # 총 상품 수 파악 → 스크롤 횟수 결정
                    total_count   = 20
                    count_element = await page.query_selector(".option_wrap.result .count em")
                    if count_element:
                        count_text = (await count_element.inner_text()).strip()
                        if count_text.isdigit():
                            total_count = int(count_text)

                    needed_scrolls = max(0, (total_count - 1) // 20)
                    for _ in range(needed_scrolls):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(SCROLL_SLEEP)
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight - 300)")
                        await asyncio.sleep(0.2)
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

                    final_items   = await page.query_selector_all(".prod_list_wrap ul.type > li")
                    batch_results = await asyncio.gather(*[
                        scrape_single_product_elements(item, target_region, target_airport, current_url)
                        for item in final_items
                    ])

                    page_count = 0
                    for res in batch_results:
                        if res:
                            raw_scraped_list.append(res)
                            page_count += 1
                    print(f"  ✅ {page_count}개 상품 수집 완료.")

                except Exception as e:
                    print(f"  ❌ URL 에러 (스킵): {current_url} → {e}")

            await browser.close()

        print(f"📦 [STAGE 1 완료] 총 {len(raw_scraped_list)}개 상품 수집.")
        save_checkpoint(raw_scraped_list)

    # ----------------------------------------------------------
    # STAGE 2: 중복 제거 (ID 기준 = 원본상품명 + 가격 + 출발공항)
    # ----------------------------------------------------------
    print("\n🧹 [STAGE 2] 상품 ID 기준 중복 제거...")
    df_raw     = pd.DataFrame(raw_scraped_list)
    before     = len(df_raw)
    df_raw     = df_raw.drop_duplicates(subset=["ID"], keep="first")
    after      = len(df_raw)
    clean_list = df_raw.to_dict(orient="records")
    print(f"  중복 제거: {before - after}개 → 최종 {after}개 고유 상품.")

    # ----------------------------------------------------------
    # STAGE 3: LLM 병렬 처리 (신규/누락 상품만)
    # ----------------------------------------------------------
    print(f"\n🤖 [STAGE 3] 신규/누락 상품 LLM 병렬 처리 (최대 {LLM_CONCURRENCY}개 동시)...")
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)

    # 캐시 히트 / LLM 필요 분류
    cached_items = []
    new_items    = []
    for item in clean_list:
        p_id         = item["ID"]
        sheet_titles = existing_titles_dict.get(p_id)
        if sheet_titles and all(str(t).strip() for t in sheet_titles):
            cached_items.append((item, sheet_titles))
        else:
            new_items.append(item)

    print(f"  캐시 히트: {len(cached_items)}개 / LLM 필요: {len(new_items)}개")

    # 신규 상품 병렬 LLM 호출
    async def process_new_item(item):
        print(f"  ✨ [LLM] {item['원본상품명']} ({item['가격']}원 / {item['출발공항']})")
        titles = await generate_naver_titles_llm(
            {
                "full_title":        item["원본상품명"],
                "region":            item["지정지역"],
                "departure_airport": item["출발공항"],
                "duration":          item["duration"],
                "description":       item["description"],
                "hashtags":          item["hashtags"],
            },
            semaphore,
        )
        return item, titles

    new_results = await asyncio.gather(*[process_new_item(item) for item in new_items])

    # 결과 합산
    final_synced_products = []
    for item, titles in cached_items:
        final_synced_products.append(_build_row(item, titles))
    for item, titles in new_results:
        final_synced_products.append(_build_row(item, list(titles)))

    # ----------------------------------------------------------
    # STAGE 4: 구글 마스터 시트 청크 업로드
    # ----------------------------------------------------------
    if not final_synced_products:
        print("\n⚠️ 업로드할 데이터 없음. 종료.")
        return

    print(f"\n🚀 [STAGE 4] 구글 마스터 시트 동기화 ({len(final_synced_products)}개)...")
    try:
        if github_sheet is None:
            target_doc   = gc.open_by_key(target_spreadsheet_id)
            github_sheet = target_doc.worksheet("github")

        column_order = [
            "ID", "원본상품명", "정제상품명", "가격", "URL", "이미지URL", "지정지역", "출발공항",
            "A_정석_1", "A_정석_2", "A_정석_3",
            "B_타겟_1", "B_타겟_2", "B_타겟_3",
            "C_혜택_1", "C_혜택_2", "C_혜택_3",
            "D_감성_1", "D_감성_2", "D_감성_3",
        ]
        df_final     = pd.DataFrame(final_synced_products)[column_order]
        data_to_upload = [df_final.columns.tolist()] + df_final.values.tolist()

        upload_to_sheet_in_chunks(github_sheet, data_to_upload)
        print(f"🎯 [완료] 총 {len(df_final)}개 상품 마스터 시트 동기화 완료.")

    except Exception as e:
        print(f"❌ 구글 시트 업로드 오류: {e}")
        raise

    # 체크포인트 정리
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("🗑️  체크포인트 파일 삭제 완료.")


def _build_row(item: dict, titles: list) -> dict:
    """상품 dict + LLM titles 리스트 → 최종 행 dict 변환"""
    return {
        "ID":        item["ID"],
        "원본상품명": item["원본상품명"],
        "정제상품명": item["정제상품명"],
        "가격":      item["가격"],
        "URL":       item["URL"],
        "이미지URL":  item["이미지URL"],
        "지정지역":   item["지정지역"],
        "출발공항":   item["출발공항"],
        "A_정석_1":  titles[0],  "A_정석_2": titles[1],  "A_정석_3": titles[2],
        "B_타겟_1":  titles[3],  "B_타겟_2": titles[4],  "B_타겟_3": titles[5],
        "C_혜택_1":  titles[6],  "C_혜택_2": titles[7],  "C_혜택_3": titles[8],
        "D_감성_1":  titles[9],  "D_감성_2": titles[10], "D_감성_3": titles[11],
    }


if __name__ == "__main__":
    asyncio.run(run_crawler())
