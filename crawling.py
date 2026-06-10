import os
import json
import asyncio
import hashlib
import datetime
import re
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

# 1. OpenAI 비동기 클라이언트 초기화
openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", "YOUR_LOCAL_API_KEY"))

async def generate_naver_titles_llm(data):
    """
    GPT-4o-mini를 활용하여 4가지 콘셉트별로 3개씩, 총 12개의 마케팅 최적화 상품명을 생성합니다.
    자체 결과물 중복 발견 시 최대 3회까지 다양성을 높여 자동 재시도(Retry)합니다.
    """
    if data['departure_airport'] != "없음":
        departure_context = f"- 지정 출발공항: {data['departure_airport']} (반드시 상품명 맨 앞에 '{data['departure_airport']}' 형식으로 고정 배치할 것)"
    else:
        departure_context = "- 지정 출발공항: 없음 (★주의: 상품명 맨 앞에 '[기본출발]', '[기본출발지]', '[출발지없음]' 등 어떠한 출발 관련 문구도 절대 넣지 말고, 곧바로 '지역명'부터 시작할 것)"

    prompt = f"""
당신은 네이버 쇼핑 검색 최적화(SEO) 및 소비자 심리를 꿰뚫는 초일류 퍼포먼스 마케팅 카피라이팅 전문가입니다.
제공된 여행 상품 데이터를 바탕으로, 가이드라인을 완벽히 준수하는 4가지 서로 다른 마케팅 콘셉트의 상품명을 각각 3개씩(총 12개) 생성하세요.

[💎 중요: 상품 등급별 키워드 의무 반영 규칙]
입력 데이터의 '원본 상품명'에 포함된 상품 등급별 괄호 문구를 파악하여, 생성되는 모든 상품명(A~D 전 콘셉트 공통)에 아래 키워드를 반드시 자연스럽게 녹여내세요.
1. 원본 상품명에 '[세이브]'가 포함된 경우: 
   - '세이브'라는 단어 자체는 쓰지 말고, 대신 [실속], [가성비추천], [합리적], [부담없는] 등 경제성과 실속을 전면 강조하는 명사 키워드를 조합하세요.
2. 원본 상품명에 '[스탠다드]'가 포함된 경우:
   - '스탠다드'라는 단어 대신 [핵심일정], [완벽구성], [알찬여행], [밸런스추천] 등 일정의 탄탄함과 균형 잡힌 구성을 강조하는 키워드를 조합하세요.
3. 원본 상품명에 '[프리미엄]'이 포함된 경우:
   - '프리미엄'이라는 단어 대신 [노쇼핑], [노팁], [노옵션], [자유시간포함], [전일정5성숙소] 등 소비자가 피로감을 느끼지 않고 가장 편안하고 고급스러운 혜택성 키워드를 전면에 배치하세요.

[⚠️ 데이터 특징 및 상품 간 차별화 지침]
현재 등록하려는 상품들은 지역명이 매우 유사합니다. 
위의 등급별 가이드라인과 더불어 [원본 상품명] 내부에 포함된 고유 힌트 및 [핵심 설명], [추출 키워드]를 분석하여 해당 상품만의 고유 특징을 상품명에 녹여내어 다른 행의 상품들과 확실하게 차별화되도록 만드세요.

[입력 데이터]
- 원본 상품명: {data['full_title']}  
- 여행 지역: {data['region']}
- 기간: {data['duration']}
{departure_context}
- 핵심 설명: {data['description']}
- 추출 키워드: {data['hashtags']}

[❌ 전 콘셉트 공통 절대 금지 가이드라인]
1. 글자 수: 모든 상품명은 공백 포함 최소 30자 ~ 최대 45자 사이로 구성한다. (50자 절대 초과 금지)
2. 중복 제거: 단일 상품명 내부에서 동일한 단어(ex: 방콕, 여행, 패키지 등)가 2회 이상 중복 나열되는 것을 절대 금지한다.
3. 정제성: '신상품', '세이브', '특가', '대박', '★' 같은 홍보성 문구나 특수문자는 절대 포함하지 않는다.
4. 출발지 조건 규칙: [지정 출발공항]이 '없음'일 경우 '기본출발' 등을 임의로 조작하지 말고 무조건 곧바로 지역명/브랜드명으로 시작한다.
5. 결과물 간 상호 중복 엄금: 생성되는 12개의 상품명은 조사나 어순만 바꾼 수준이 아니라 완전히 다른 키워드 조합을 가져야 한다.

[🎯 콘셉트별 상세 생성 규칙]
■ 콘셉트 A (정석 SEO형 - 3개): 핵심 키워드 위주의 명사 나열 조합. (3개 간 키워드 배치 순서를 다르게 뒤섞을 것)
■ 콘셉트 B (타겟/상황형 - 3개): 타겟 키워드를 3개가 각각 다르게 선택 (부모님 효도, 아이동반 등)
■ 콘셉트 C (혜택/USP형 - 3개): 소비자가 직관적으로 이득을 느끼는 등급별 프리미엄 혜택 명사화 강조.
■ 콘셉트 D (감성/트렌디형 - 3개): 요즘뜨는, 인생샷, 감성숙소 등 감성 단어가 겹치지 않게 분산.
"""
    
    json_schema_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "naver_twelve_titles_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "A_1": {"type": "string"}, "A_2": {"type": "string"}, "A_3": {"type": "string"},
                    "B_1": {"type": "string"}, "B_2": {"type": "string"}, "B_3": {"type": "string"},
                    "C_1": {"type": "string"}, "C_2": {"type": "string"}, "C_3": {"type": "string"},
                    "D_1": {"type": "string"}, "D_2": {"type": "string"}, "D_3": {"type": "string"}
                },
                "required": [
                    "A_1", "A_2", "A_3", "B_1", "B_2", "B_3", 
                    "C_1", "C_2", "C_3", "D_1", "D_2", "D_3"
                ],
                "additionalProperties": False
            }
        }
    }

    max_retries = 3
    current_temp = 0.4
    
    for attempt in range(1, max_retries + 1):
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that outputs compliant JSON based on the provided schema."},
                    {"role": "user", "content": prompt}
                ],
                response_format=json_schema_format,
                temperature=current_temp,
                seed=42 if attempt == 1 else None
            )
            
            res_json = json.loads(response.choices[0].message.content)
            titles_list = [
                res_json.get(f"{concepts}_{i}", "").strip() 
                for concepts in ['A', 'B', 'C', 'D'] 
                for i in [1, 2, 3]
            ]
            
            unique_titles = set(titles_list)
            if len(unique_titles) == 12:
                return tuple(titles_list)
            
            current_temp += 0.15
            
        except Exception as e:
            if attempt == max_retries:
                break

    err_t = f"[Error] {data['full_title'][:15]}"
    if 'titles_list' not in locals() or len(titles_list) < 12:
        titles_list = [err_t] * 12
    return tuple(titles_list)


async def scrape_single_product_elements(item, target_region, target_airport, current_url, idx):
    """
    [1단계 전용] 외부 브라우저 대기를 없애기 위해 오직 '웹 엘리먼트 순수 크롤링'만 수행합니다.
    """
    try:
        main_info = await item.query_selector(":scope > .inr.right")
        img_check = await item.query_selector(":scope > .inr.img")
        
        if not main_info or not img_check:
            return None

        title_el = await main_info.query_selector(".item_title")
        full_title = (await title_el.inner_text()).strip() if title_el else "제목 없음"

        price_el = await main_info.query_selector(".price")
        price_raw = await price_el.inner_text() if price_el else "0"
        price = "".join(filter(str.isdigit, price_raw))

        # 고유 ID 생성 (제목+가격+주소+순번)
        unique_str = f"{full_title}_{price}_{current_url}_{idx}"
        product_id = hashlib.md5(unique_str.encode()).hexdigest()[:8]

        pure_title_body = re.sub(r'\[.*?\]', '', full_title).strip()
        if "#" in pure_title_body:
            parts = pure_title_body.split("#")
            pure_title = parts[0].strip()
            title_hashtags = sorted([p.strip() for p in parts[1:] if p.strip()])
        else:
            pure_title = pure_title_body
            title_hashtags = []

        hash_span_els = await main_info.query_selector_all(".hash_group span")
        ui_hashtags = [(await h.inner_text()).replace("#", "").strip() for h in hash_span_els]
        all_hashtags = sorted(list(set(title_hashtags + ui_hashtags)))

        desc_el = await main_info.query_selector(".item_text.stit")
        product_desc = (await desc_el.inner_text()).strip() if desc_el else ""

        duration_el = await main_info.query_selector("span.icn.cal")
        duration_text = (await duration_el.inner_text()).strip() if duration_el else ""
        duration = duration_text.replace("여행기간", "").strip()

        img_url = ""
        img_el = await img_check.query_selector("img")
        if img_el:
            data_src = await img_el.get_attribute("data-src")
            src = await img_el.get_attribute("src")
            potential_url = data_src if data_src else src
            
            if potential_url and "bg_alpha" not in potential_url:
                img_url = potential_url.strip()
            else:
                all_imgs = await img_check.query_selector_all("img")
                for im in all_imgs:
                    i_src = await im.get_attribute("src")
                    i_data = await im.get_attribute("data-src")
                    target = i_data if i_data else i_src
                    if target and "bg_alpha" not in target:
                        img_url = target.strip()
                        break

        if img_url and img_url.startswith("//"): 
            img_url = "https:" + img_url

        return {
            "ID": product_id,
            "원본상품명": full_title,
            "정제상품명": pure_title,
            "가격": int(price) if price else 0,
            "URL": current_url,
            "이미지URL": img_url,
            "지정지역": target_region,
            "출발공항": target_airport,
            "duration": duration,
            "description": product_desc,
            "hashtags": ", ".join(all_hashtags)
        }
    except Exception as e:
        print(f"⚠️ 개별 상품 파싱 실패 패스: {e}")
        return None


async def run_crawler():
    print("🌐 구글 API 인증 및 스프레드시트 연결 중...")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    json_raw = os.environ.get("GOOGLE_JSON_RAW")
    
    try:
        if json_raw:
            service_account_info = json.loads(json_raw)
            creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        else:
            creds = Credentials.from_service_account_file('secrets.json', scopes=scopes)
        gc = gspread.authorize(creds)
    except Exception as auth_error:
        print(f"❌ 구글 API 인증 실패: {auth_error}")
        return

    # SOURCE LOAD
    source_spreadsheet_id = os.environ.get("SOURCE_SPREADSHEET_ID")
    try:
        source_doc = gc.open_by_key(source_spreadsheet_id)
        source_sheet = source_doc.worksheet("상품리스트")
        all_rows = source_sheet.get_all_values()
        target_tasks = []
        for row in all_rows[1:]:
            if len(row) >= 1 and row[0].startswith("http"):
                target_tasks.append({
                    "url": row[0].strip(),
                    "sheet_region": row[1].strip() if len(row) > 1 and row[1].strip() else "지역명 미상",
                    "sheet_airport": row[2].strip() if len(row) > 2 and row[2].strip() else "없음"
                })
        print(f"✅ 총 {len(target_tasks)}개의 대용량 타겟 URL 작업을 확보했습니다.")
    except Exception as e:
        print(f"❌ URL 리스트 가공 에러: {e}")
        return

    # TARGET 기존 마스터 캐시 LOAD
    target_spreadsheet_id = os.environ.get("TARGET_SPREADSHEET_ID")
    worksheet_name = "github"
    existing_titles_dict = {}
    
    try:
        target_doc = gc.open_by_key(target_spreadsheet_id)
        github_sheet = target_doc.worksheet(worksheet_name)
        existing_data = github_sheet.get_all_records()
        for r in existing_data:
            if r.get("ID"):
                existing_titles_dict[str(r["ID"])] = [
                    r.get("A_정석_1", ""), r.get("A_정석_2", ""), r.get("A_정석_3", ""),
                    r.get("B_타겟_1", ""), r.get("B_타겟_2", ""), r.get("B_타겟_3", ""),
                    r.get("C_혜택_1", ""), r.get("C_혜택_2", ""), r.get("C_혜택_3", ""),
                    r.get("D_감성_1", ""), r.get("D_감성_2", ""), r.get("D_감성_3", "")
                ]
        print(f"✅ 기수집된 마스터 데이터 {len(existing_titles_dict)}개를 캐싱했습니다.")
    except Exception as cache_error:
        print(f"⚠️ 기존 시트 로드 패스: {cache_error}")

    # =======================================================================
    # 🌟 [1단계] 고속 웹 크롤링 스테이지 (GPT 대기 없음, 오직 순수 스크래핑만)
    # =======================================================================
    print("\n⚡ [STAGE 1] 전체 기획전 URL 대상 고속 웹 스크래핑을 시작합니다...")
    raw_scraped_list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 1024},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        for idx, task in enumerate(target_tasks, start=1):
            current_url = task["url"]
            target_region = task["sheet_region"]
            target_airport = task["sheet_airport"]
            
            try:
                print(f"🔄 [{idx}/{len(target_tasks)}] {target_region} 기획전 스크래핑 중...")
                await page.goto(current_url, wait_until="domcontentloaded", timeout=25000)
                
                try:
                    await page.wait_for_selector(".option_wrap.result .count em", timeout=5000)
                except:
                    pass

                total_count = 20
                count_element = await page.query_selector(".option_wrap.result .count em")
                if count_element:
                    count_text = (await count_element.inner_text()).strip()
                    if count_text.isdigit():
                        total_count = int(count_text)

                needed_scrolls = (total_count - 1) // 20 if total_count > 20 else 0
                for _ in range(needed_scrolls):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(1.2)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight - 300)")
                    await asyncio.sleep(0.2)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

                final_items = await page.query_selector_all(".prod_list_wrap ul.type > li")
                
                # 병렬 스크래핑 태스크 빌드
                tasks = [
                    scrape_single_product_elements(item, target_region, target_airport, current_url, i)
                    for i, item in enumerate(final_items)
                ]
                batch_results = await asyncio.gather(*tasks)
                
                for res in batch_results:
                    if res:
                        raw_scraped_list.append(res)
                        
            except Exception as e:
                print(f"❌ URL 패스 에러: {current_url} -> {e}")
                continue
                
        await browser.close()

    print(f"📦 [STAGE 1 완료] 총 {len(raw_scraped_list)}개의 웹 상품 원본 데이터를 정상 수집했습니다.")

    # =======================================================================
    # 🌟 [2단계 & 3단계] 데이터 병목 정제 및 세션 분리형 LLM 조립 스테이지
    # =======================================================================
    print("\n🤖 [STAGE 2 & 3] 기존 시트 비교 필터링 및 조건부 LLM 연산을 시작합니다...")
    
    final_synced_products = []
    runtime_titles_dict = {}
    
    # 중복 노출 상품 대량 유입 시 'URL' 기준 1차 청소 처리
    df_raw = pd.DataFrame(raw_scraped_list)
    df_raw = df_raw.drop_duplicates(subset=["URL"], keep="first")
    clean_scraped_list = df_raw.to_dict(orient="records")
    print(f"🧹 기획전 간 중복 노출되던 상품을 제외한 [{len(clean_scraped_list)}개] 고유 상품 최종 분석 개시.")

    for current_item in clean_scraped_list:
        p_id = current_item["ID"]
        f_title = current_item["원본상품명"]
        price = current_item["가격"]

        is_cached = False
        titles = None

        # 구글 시트에 완벽히 존재하며 공백이 없는 데이터인지 교차 체크
        if p_id in existing_titles_dict:
            sheet_titles = existing_titles_dict[p_id]
            if sheet_titles and all(str(t).strip() for t in sheet_titles):
                titles = sheet_titles
                is_cached = True

        # 동일 회차 내 런타임 ID 캐시 디펜스
        if not is_cached and p_id in runtime_titles_dict:
            titles = runtime_titles_dict[p_id]
            is_cached = True

        # [핵심] 기존 상품 패스 및 신규/미완성 상품만 선별하여 GPT 호출
        if not is_cached or titles is None:
            print(f"✨ [LLM 연산 할당] {f_title} ({price}원)")
            ai_input_data = {
                "full_title": f_title,
                "region": current_item["지정지역"],
                "departure_airport": current_item["출발공항"],
                "duration": current_item["duration"],
                "description": current_item["description"],
                "hashtags": current_item["hashtags"]
            }
            # 등급 키워드가 적용된 최적화 카피라이터 호출
            titles = await generate_naver_titles_llm(ai_input_data)
            runtime_titles_dict[p_id] = titles
            # OpenAI 가속 조절을 위한 0.1초 미세 딜레이
            await asyncio.sleep(0.1)

        # 수집 및 AI 조합 결과 최종 융합
        final_synced_products.append({
            "ID": p_id,
            "원본상품명": f_title,
            "정제상품명": current_item["정제상품명"],
            "가격": price,
            "URL": current_item["URL"],
            "이미지URL": current_item["이미지URL"],
            "지정지역": current_item["지정지역"],
            "출발공항": current_item["출발공항"],
            "A_정석_1": titles[0], "A_정석_2": titles[1], "A_정석_3": titles[2],
            "B_타겟_1": titles[3], "B_타겟_2": titles[4], "B_타겟_3": titles[5],
            "C_혜택_1": titles[6], "C_혜택_2": titles[7], "C_혜택_3": titles[8],
            "D_감성_1": titles[9], "D_감성_2": titles[10], "D_감성_3": titles[11]
        })

    # =======================================================================
    # 🌟 [4단계] 구글 마스터 시트 원샷 통적재 (API 할당량 429 에러 100% 차단)
    # =======================================================================
    if final_synced_products:
        print("\n🚀 [STAGE 4] 구글 마스터 시트 원샷 동기화 업데이트 시작...")
        try:
            df_final = pd.DataFrame(final_synced_products)
            column_order = [
                "ID", "원본상품명", "정제상품명", "가격", "URL", "이미지URL", "지정지역", "출발공항",
                "A_정석_1", "A_정석_2", "A_정석_3", "B_타겟_1", "B_타겟_2", "B_타겟_3",
                "C_혜택_1", "C_혜택_2", "C_혜택_3", "D_감성_1", "D_감성_2", "D_감성_3"
            ]
            df_final = df_final[column_order]
            
            data_to_upload = [df_final.columns.values.tolist()] + df_final.values.tolist()

            github_sheet.clear()
            github_sheet.update(values=data_to_upload, range_name='A1')
            print(f"🎯 [최종 대동기화 성공] 마스터 Raw 시트 동기화 완료! (총 {len(df_final)}개 데이터 보존 및 동기화 적재)")

        except Exception as e:
            print(f"❌ 구글 시트 마스터 적재 치명적 오류: {e}")

if __name__ == "__main__":
    async_loop = asyncio.get_event_loop()
    async_loop.run_until_complete(run_crawler())
