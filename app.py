import difflib
import io
import json
import logging
import os
import secrets

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from google import genai
from google.genai import types

from tools import TOOL_CATALOG, TOOL_MAP, run_tool

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

COLUMNS = [
    "조직분류코드",
    "교과목번호",
    "교과목명",
    "분반",
    "이수구분",
    "학점",
    "이론",
    "실습",
    "개설학과",
    "대상학년",
    "신청_전",
    "신청_1",
    "신청_2",
    "신청_3",
    "신청_4",
    "제한_전",
    "제한_1",
    "제한_2",
    "제한_3",
    "제한_4",
    "시간표",
    "대표교수직급",
    "폐강일자",
    "폐강사유",
]

NUMERIC_COLUMNS = [
    "학점",
    "이론",
    "실습",
    "신청_전",
    "신청_1",
    "신청_2",
    "신청_3",
    "신청_4",
    "제한_전",
    "제한_1",
    "제한_2",
    "제한_3",
    "제한_4",
]

# 업로드 파일은 디스크에 저장하지 않고 메모리에만 1개 데이터셋 보관
STATE = {
    "df": None,
    "filename": None,
    "inspect_summary": None,
    "last_result": None,
}


def _to_json_safe_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame을 JSON 직렬화 가능한 딕셔너리 리스트로 변환 (NaN -> None)."""
    clean = df.astype(object).where(pd.notna(df), None)
    return clean.to_dict(orient="records")


def _find_similar_candidates(df: pd.DataFrame, target_cols: list[str]) -> list[dict]:
    """텍스트 열에서 공백·표현 차이로 같은 뜻처럼 보이는 값 쌍을 찾는다."""
    candidates = []
    for col in target_cols:
        series = df[col].dropna().astype(str).str.strip()
        series = series[series != ""]
        counts = series.value_counts().to_dict()
        values = list(counts.keys())

        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                a, b = values[i], values[j]
                no_space_match = a.replace(" ", "") == b.replace(" ", "")
                ratio = difflib.SequenceMatcher(None, a, b).ratio()
                if no_space_match or ratio >= 0.75:
                    candidates.append(
                        {
                            "column": col,
                            "value_a": a,
                            "count_a": int(counts[a]),
                            "value_b": b,
                            "count_b": int(counts[b]),
                            "reason": "띄어쓰기만 다름" if no_space_match else "표현이 매우 비슷함",
                        }
                    )
    return candidates


def _build_inspect_summary(
    filename: str, raw_rows: int, df: pd.DataFrame, numeric_issues: dict
) -> dict:
    """진단 요약 정보와 경고 목록을 생성한다 (AI에게 보낼 수 있는 안전한 요약)."""
    # 빈칸 점검
    missing_by_column = {}
    for col in COLUMNS:
        s = df[col]
        if col in NUMERIC_COLUMNS:
            empty_cnt = int(s.isna().sum())
        else:
            empty_cnt = int((s.isna() | (s.astype(str).str.strip() == "")).sum())
        if empty_cnt > 0:
            missing_by_column[col] = {
                "count": empty_cnt,
                "note": "해당 없음(미폐강 강좌)" if col in ("폐강일자", "폐강사유") else "누락 또는 해당 없음",
            }

    # 분반·과목 수·폐강 수
    closed_mask = df["폐강일자"].notna() & (df["폐강일자"].astype(str).str.strip() != "")
    closed_rows = int(closed_mask.sum())
    open_rows = int((~closed_mask).sum())
    unique_sections = int(
        df[["교과목번호", "분반"]].astype(str).drop_duplicates().shape[0]
    )
    unique_course_codes = int(df["교과목번호"].astype(str).nunique())
    unique_course_names = int(df["교과목명"].astype(str).nunique())

    # 단과대 목록
    colleges_series = df["단과대"].dropna().astype(str)
    college_counts = {str(k): int(v) for k, v in colleges_series.value_counts().items()}

    # 미폐강 강좌 중 제한_전 == 0 또는 빈칸 개수
    zero_limit_open = int(
        ((~closed_mask) & (df["제한_전"].isna() | (df["제한_전"] == 0))).sum()
    )

    # 비슷한 값 후보
    similar_candidates = _find_similar_candidates(
        df, ["이수구분", "대표교수직급", "폐강사유"]
    )

    # 경고 목록 구성
    warnings = [
        {
            "type": "열 이름 2줄 처리",
            "message": "1~2행이 병합된 2단 머리글이어서 3행부터 데이터로 읽고 24개 열 이름을 직접 지정했습니다.",
        },
        {
            "type": "글자 숫자 변환",
            "message": "숫자 열 13개(학점, 이론, 실습, 신청_*, 제한_*)가 글자로 저장되어 있어 계산용 숫자로 변환했습니다.",
        },
    ]

    if numeric_issues or zero_limit_open > 0:
        parts = []
        for col, info in numeric_issues.items():
            if info["convert_fail_count"] > 0:
                parts.append(
                    f"{col} 변환 실패 {info['convert_fail_count']}건(행: {info['convert_fail_excel_rows']})"
                )
            if info["negative_count"] > 0:
                parts.append(
                    f"{col} 음수 {info['negative_count']}건(행: {info['negative_excel_rows']})"
                )
        if zero_limit_open > 0:
            parts.append(f"미폐강 중 제한_전=0 또는 빈칸 {zero_limit_open}건(충원율 나눗셈 주의)")
        warnings.append(
            {
                "type": "비정상 값",
                "message": "원본에서 비정상 수치/예외 값이 발견되었습니다: " + ", ".join(parts),
            }
        )

    if missing_by_column:
        miss_desc = ", ".join(
            f"{col} {info['count']}건({info['note']})"
            for col, info in missing_by_column.items()
        )
        warnings.append(
            {
                "type": "빈칸",
                "message": f"빈칸이 있는 열: {miss_desc}",
            }
        )

    if similar_candidates:
        sim_desc = ", ".join(
            f"{c['column']}: '{c['value_a']}'({c['count_a']}건) vs '{c['value_b']}'({c['count_b']}건)"
            for c in similar_candidates
        )
        warnings.append(
            {
                "type": "비슷한 값 후보",
                "message": f"같은 뜻인데 표기가 달라 따로 세어질 수 있는 값: {sim_desc}",
            }
        )

    if unique_sections < len(df):
        warnings.append(
            {
                "type": "중복 분반 주의",
                "message": (
                    f"전체 데이터는 {len(df)}행이지만 고유한 (교과목번호+분반)은 {unique_sections}개입니다. "
                    "같은 분반이 개설학과별로 여러 줄 나뉘어 있을 수 있습니다."
                ),
            }
        )

    return {
        "filename": filename,
        "raw_rows": int(raw_rows),
        "data_rows": int(len(df)),
        "col_count": len(COLUMNS),
        "columns": COLUMNS,
        "open_rows": open_rows,
        "closed_rows": closed_rows,
        "unique_sections": unique_sections,
        "unique_course_codes": unique_course_codes,
        "unique_course_names": unique_course_names,
        "college_count": len(college_counts),
        "colleges": college_counts,
        "numeric_issues": numeric_issues,
        "missing_by_column": missing_by_column,
        "similar_candidates": similar_candidates,
        "warnings": warnings,
    }


def _require_auth():
    """비밀번호 인증을 통과했는지 확인한다."""
    if not session.get("authenticated"):
        return jsonify({"error": "먼저 비밀번호(8자리 이상)를 입력해 잠금을 해제해 주세요."}), 401
    return None


@app.get("/")
def index():
    """메인 웹앱 화면을 반환한다."""
    return render_template("index.html")


@app.get("/auth/status")
def auth_status():
    """현재 로그인 상태와 비밀번호 설정 여부를 반환한다."""
    load_dotenv(override=True)
    expected = os.getenv("APP_PASSWORD", "").strip()
    return jsonify(
        {
            "authenticated": bool(session.get("authenticated")),
            "password_configured": True,
            "using_env_password": len(expected) >= 8,
            "min_length": 8,
        }
    )


@app.post("/login")
def login():
    """8자리 이상 비밀번호를 확인하고 세션을 연다."""
    load_dotenv(override=True)
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", "")).strip()

    if len(password) < 8:
        return jsonify({"error": "비밀번호는 8자리 이상 입력해야 합니다."}), 400

    expected = os.getenv("APP_PASSWORD", "").strip()
    if len(expected) >= 8 and not secrets.compare_digest(password, expected):
        logger.warning("비밀번호 불일치 로그인 시도")
        return jsonify({"error": "비밀번호가 올바르지 않습니다."}), 401

    session["authenticated"] = True
    logger.info("비밀번호 인증 성공")
    return jsonify({"authenticated": True})


@app.post("/logout")
def logout():
    """세션을 잠근다."""
    session.pop("authenticated", None)
    return jsonify({"authenticated": False})


@app.get("/health")
def health():
    """서버가 정상 동작 중인지 확인하는 상태 점검 경로."""
    logger.info("GET /health 요청 처리")
    return jsonify(
        {
            "status": "ok",
            "has_api_key": bool(os.getenv("GEMINI_API_KEY")),
        }
    )


@app.post("/inspect")
def inspect_file():
    """엑셀(.xls) 파일을 메모리에 읽고 1차 변환 및 2차 진단을 수행한다."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "업로드할 파일을 선택해 주세요."}), 400

    filename = uploaded.filename
    if not filename.lower().endswith(".xls"):
        return (
            jsonify(
                {
                    "error": "구형 엑셀(.xls) 파일만 업로드할 수 있습니다. 파일 형식을 확인해 주세요."
                }
            ),
            400,
        )

    try:
        file_bytes = uploaded.read()
        if not file_bytes:
            return jsonify({"error": "빈 파일입니다. 내용이 있는 .xls 파일을 올려 주세요."}), 400

        raw_df = pd.read_excel(io.BytesIO(file_bytes), header=None, engine="xlrd")
    except Exception as exc:
        logger.exception("엑셀 파일 읽기 실패: %s", filename)
        return (
            jsonify(
                {
                    "error": f"엑셀 파일을 읽는 중 문제가 발생했습니다. 올바른 .xls 파일인지 확인해 주세요. ({exc})"
                }
            ),
            400,
        )

    if raw_df.shape[0] < 3 or raw_df.shape[1] != len(COLUMNS):
        return (
            jsonify(
                {
                    "error": (
                        f"예상한 표 형식과 다릅니다. 최소 3행 이상, {len(COLUMNS)}열이어야 하는데 "
                        f"현재 {raw_df.shape[0]}행 × {raw_df.shape[1]}열입니다."
                    )
                }
            ),
            400,
        )

    # 1~2행(인덱스 0, 1)은 병합된 2단 열 이름이므로 3행(인덱스 2)부터 데이터로 사용
    df = raw_df.iloc[2:].copy().reset_index(drop=True)
    df.columns = COLUMNS

    # 숫자 열 변환 (원본 이상치는 몰래 고치지 않고 변환 실패·음수 위치만 기록)
    numeric_issues = {}
    for col in NUMERIC_COLUMNS:
        original = df[col]
        non_empty_mask = original.notna() & (original.astype(str).str.strip() != "")
        converted = pd.to_numeric(original, errors="coerce")
        df[col] = converted

        fail_mask = non_empty_mask & converted.isna()
        neg_mask = converted.notna() & (converted < 0)

        fail_rows = [int(i) + 3 for i in df.index[fail_mask]]
        neg_rows = [int(i) + 3 for i in df.index[neg_mask]]

        if fail_rows or neg_rows:
            numeric_issues[col] = {
                "convert_fail_count": len(fail_rows),
                "convert_fail_excel_rows": fail_rows[:20],
                "negative_count": len(neg_rows),
                "negative_excel_rows": neg_rows[:20],
            }

    # 단과대 파생 열 생성 (개설학과를 '>>'로 나눈 첫 부분)
    df["단과대"] = (
        df["개설학과"]
        .fillna("")
        .astype(str)
        .str.split(">>")
        .str[0]
        .str.strip()
        .replace("", None)
    )

    summary = _build_inspect_summary(filename, raw_df.shape[0], df, numeric_issues)

    STATE["df"] = df
    STATE["filename"] = filename
    STATE["inspect_summary"] = summary
    STATE["last_result"] = None

    logger.info(
        "POST /inspect 2차 완료: %s (데이터 %d행, 단과대 %d개, 경고 %d건)",
        filename,
        len(df),
        summary["college_count"],
        len(summary["warnings"]),
    )

    return jsonify(
        {
            **summary,
            "preview": _to_json_safe_records(df[COLUMNS].head(5)),
        }
    )


@app.post("/suggest")
def suggest_analyses():
    """진단 요약과 도구 목록만 Gemini에 보내 3~5개의 분석 후보(JSON)를 제안받는다."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err
    summary = STATE.get("inspect_summary")
    if summary is None:
        return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드하고 진단을 완료해 주세요."}), 400

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return (
            jsonify(
                {
                    "error": "GEMINI_API_KEY가 설정되어 있지 않습니다. 폴더 안의 .env 파일을 확인해 주세요."
                }
            ),
            400,
        )

    prompt = (
        "너는 대학 학사 데이터를 분석하는 행정 데이터 조수다.\n"
        "아래 [진단 요약]과 [허용된 도구 목록]만 보고, 이 데이터에서 바로 해볼 만한 분석 후보 3~5개를 골라 JSON으로 제안해라.\n"
        "규칙:\n"
        "1. 반드시 허용된 도구 목록에 있는 tool_name만 사용해라. 목록에 없는 도구나 임의의 분석 이름은 절대 쓰지 마라.\n"
        "2. 출력은 반드시 JSON 배열([{...}, ...]) 또는 {\"suggestions\": [...]} 형태로만 응답해라.\n"
        "3. 각 항목은 tool_name(문자열), reason(한국어 이유 1~2문장), columns(사용할 열 이름 배열), params(선택적 딕셔너리)를 포함해야 한다.\n\n"
        f"[허용된 도구 목록]\n{json.dumps(list(TOOL_CATALOG.values()), ensure_ascii=False, indent=2)}\n\n"
        f"[진단 요약]\n{json.dumps(summary, ensure_ascii=False, indent=2)}"
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        raw_text = (response.text or "").strip()
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.exception("Gemini /suggest 응답 JSON 파싱 실패")
        return (
            jsonify(
                {
                    "error": "AI가 올바른 JSON 형식으로 제안하지 못했습니다. 잠시 후 다시 시도해 주세요."
                }
            ),
            502,
        )
    except Exception as exc:
        logger.exception("Gemini /suggest 호출 실패")
        return (
            jsonify(
                {
                    "error": f"AI 제안 생성 중 오류가 발생했습니다: {exc}"
                }
            ),
            502,
        )

    items = parsed.get("suggestions", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return jsonify({"error": "AI 제안 형식이 올바르지 않습니다."}), 502

    accepted = []
    rejected = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tname = item.get("tool_name")
        if tname in TOOL_MAP:
            accepted.append(
                {
                    "tool_name": tname,
                    "reason": str(item.get("reason", "")),
                    "columns": item.get("columns")
                    if isinstance(item.get("columns"), list)
                    else TOOL_CATALOG[tname]["default_columns"],
                    "params": item.get("params") if isinstance(item.get("params"), dict) else {},
                }
            )
        else:
            rejected.append(str(tname))

    if rejected:
        logger.warning("도구 목록 밖의 AI 제안 거절됨: %s", rejected)

    accepted = accepted[:5]
    if not accepted:
        return (
            jsonify(
                {
                    "error": "AI가 허용된 도구 목록 안의 분석을 제안하지 못했습니다. 다시 시도해 주세요."
                }
            ),
            502,
        )

    logger.info("POST /suggest 완료: 채택 %d건, 거절 %d건", len(accepted), len(rejected))
    return jsonify(
        {
            "suggestions": accepted,
            "rejected_tools": rejected,
        }
    )


@app.post("/run")
def run_analyses():
    """사용자가 선택한 도구를 pandas로 실행하고 결과를 메모리에 보관한다."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err
    df = STATE.get("df")
    if df is None:
        return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드해 주세요."}), 400

    body = request.get_json(silent=True) or {}
    selections = body.get("selections")
    if selections is None and body.get("tool_name"):
        selections = [{"tool_name": body.get("tool_name"), "params": body.get("params") or {}}]

    if not isinstance(selections, list) or len(selections) == 0:
        return jsonify({"error": "실행할 분석 도구를 하나 이상 선택해 주세요."}), 400

    results = []
    for item in selections:
        if isinstance(item, str):
            tname, params = item, {}
        elif isinstance(item, dict):
            tname = item.get("tool_name")
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
        else:
            return jsonify({"error": "선택 항목 형식이 올바르지 않습니다."}), 400

        if tname not in TOOL_MAP:
            return jsonify({"error": f"허용되지 않은 분석 도구입니다: {tname}"}), 400

        try:
            res = run_tool(df, tname, params)
            results.append(res)
        except Exception as exc:
            logger.exception("도구 실행 실패: %s", tname)
            return jsonify({"error": f"'{tname}' 계산 중 오류가 발생했습니다: {exc}"}), 500

    payload = {
        "filename": STATE.get("filename"),
        "results": results,
    }
    STATE["last_result"] = payload

    logger.info("POST /run 완료: %d개 도구 실행", len(results))
    return jsonify(payload)


@app.post("/ask")
def ask_question():
    """사용자의 자유 질문을 AI가 도구+필터 JSON으로 번역하고, pandas가 직접 계산한다."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err

    df = STATE.get("df")
    summary = STATE.get("inspect_summary")
    if df is None or summary is None:
        return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드하고 진단을 완료해 주세요."}), 400

    body = request.get_json(silent=True) or {}
    question = str(body.get("question", "")).strip()
    if not question:
        return jsonify({"error": "분석할 질문을 입력해 주세요."}), 400

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인해 주세요."}), 400

    # 원본 행은 절대 보내지 않고, 열 이름과 범주형 고유값 목록만 전달
    schema_meta = {
        "available_columns": list(df.columns),
        "categorical_values": {
            "단과대": sorted([str(x) for x in df["단과대"].dropna().unique().tolist()]),
            "이수구분": sorted([str(x) for x in df["이수구분"].dropna().unique().tolist()]),
            "대상학년": sorted([str(x) for x in df["대상학년"].dropna().unique().tolist()]),
            "대표교수직급": sorted([str(x) for x in df["대표교수직급"].dropna().unique().tolist()]),
        },
        "allowed_filter_ops": ["==", "!=", "contains", ">", ">=", "<", "<="],
    }

    prompt = (
        "너는 대학 학사 데이터 질문을 기존 계산 도구 주문서(JSON)로 번역하는 통역사다.\n"
        "아래 [허용된 도구 목록]과 [데이터 열/범주 정보]만 보고 사용자의 [질문]을 JSON으로 변환하라.\n"
        "규칙:\n"
        "1. 파일에 없는 정보(예: 교수 이름, 학생 성적, 강의평 등)를 물으면 answerable=false로 하고 cannot_reason에 이유를 적어라.\n"
        "2. answerable=true일 때 tool_name은 반드시 [허용된 도구 목록] 중 하나여야 한다.\n"
        "3. 조건(예: 특정 단과대, 특정 학년, 특정 이수구분, 특정 과목명 포함)이 있으면 params.filters 배열에 "
        "[{\"column\": \"열이름\", \"op\": \"==|!=|contains|>|>=|<|<=\", \"value\": \"값\"}] 형태로 넣어라.\n"
        "4. 각 도구별 주요 params: group_rate(group_by: 단과대|이수구분|대상학년), "
        "top_bottom_n(metric: 신청_전|제한_전|학점, n: 정수, exclude_closed: true|false), "
        "compare_one_vs_all(group_col: 단과대|이수구분|대상학년, target_value: 값).\n"
        "5. 반드시 아래 JSON 형식으로만 응답하라:\n"
        "{\"answerable\": true, \"cannot_reason\": \"\", \"tool_name\": \"...\", \"params\": {}, \"explanation\": \"한국어 1줄 설명\"}\n\n"
        f"[허용된 도구 목록]\n{json.dumps(list(TOOL_CATALOG.values()), ensure_ascii=False, indent=2)}\n\n"
        f"[데이터 열/범주 정보]\n{json.dumps(schema_meta, ensure_ascii=False, indent=2)}\n\n"
        f"[질문]\n{question}"
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        plan = json.loads((response.text or "").strip())
    except Exception as exc:
        logger.exception("Gemini /ask 해석 실패")
        return jsonify({"error": f"질문을 해석하는 중 오류가 발생했습니다: {exc}"}), 502

    if not isinstance(plan, dict):
        return jsonify({"error": "AI 질문 해석 형식이 올바르지 않습니다."}), 502

    if not plan.get("answerable", False):
        reason = plan.get("cannot_reason") or "이 파일에 있는 열만으로는 해당 질문에 답할 수 없습니다."
        logger.info("POST /ask 답변 불가 질문: %s", reason)
        return jsonify({"answerable": False, "cannot_reason": reason}), 400

    tname = plan.get("tool_name")
    if tname not in TOOL_MAP:
        logger.warning("도구 목록 밖의 /ask 제안 거절: %s", tname)
        return jsonify({"error": f"허용되지 않은 분석 도구({tname})가 선택되어 실행을 중단했습니다."}), 400

    params = plan.get("params") if isinstance(plan.get("params"), dict) else {}
    try:
        res = run_tool(df, tname, params)
    except Exception as exc:
        logger.exception("POST /ask 도구 실행 실패: %s", tname)
        return jsonify({"error": f"질문 조건으로 계산하는 중 오류가 발생했습니다: {exc}"}), 400

    payload = {
        "filename": STATE.get("filename"),
        "results": [res],
    }
    STATE["last_result"] = payload

    logger.info("POST /ask 완료: 질문='%s' -> 도구=%s", question, tname)
    return jsonify(
        {
            "answerable": True,
            "question": question,
            "explanation": str(plan.get("explanation", "")),
            "tool_name": tname,
            "params": params,
            "results": [res],
        }
    )


@app.post("/explain")
def explain_results():
    """모드 A(비교용: 진단 요약만) 또는 모드 B(정식: 계산 결과 JSON만)로 보고서를 생성한다."""
    auth_err = _require_auth()
    if auth_err:
        return auth_err
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode", "B")).strip().upper()
    if mode not in ("A", "B"):
        return jsonify({"error": "보고서 모드는 'A' 또는 'B'만 선택할 수 있습니다."}), 400

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return (
            jsonify(
                {
                    "error": "GEMINI_API_KEY가 설정되어 있지 않습니다. 폴더 안의 .env 파일을 확인해 주세요."
                }
            ),
            400,
        )

    if mode == "A":
        summary = STATE.get("inspect_summary")
        if summary is None:
            return jsonify({"error": "먼저 엑셀(.xls) 파일을 업로드하고 진단을 완료해 주세요."}), 400
        prompt = (
            "아래 대학 개설강좌 데이터 진단 요약을 보고, 이 데이터를 분석해서 설명해 줘.\n\n"
            f"[진단 요약]\n{json.dumps(summary, ensure_ascii=False, indent=2)}"
        )
    else:
        last_result = STATE.get("last_result")
        if last_result is None:
            return (
                jsonify(
                    {
                        "error": "모드 B 정식 보고서를 만들려면 먼저 분석 도구를 선택해 계산(/run)을 실행해 주세요."
                    }
                ),
                400,
            )
        prompt = (
            "너는 대학 행정 보고서를 작성하는 데이터 해석가다.\n"
            "아래 [계산 결과 JSON]만 보고 마크다운 형식의 정식 보고서를 작성해라.\n"
            "반드시 지켜야 할 규칙:\n"
            "- 주어진 숫자만 인용하라. 새 숫자를 만들거나 다시 계산하지 마라. 파일만으로 알 수 없는 것은 알 수 없다고 써라.\n"
            "- 아래 7개 제목 순서대로 작성하라:\n"
            "  1. 무엇을 봤는가\n"
            "  2. 기준(정의)과 사용한 열\n"
            "  3. 제외된 항목\n"
            "  4. 계산 결과\n"
            "  5. 읽어낼 수 있는 것\n"
            "  6. 이 데이터로는 알 수 없는 것\n"
            "  7. 다음에 확인해볼 것\n\n"
            f"[계산 결과 JSON]\n{json.dumps(last_result, ensure_ascii=False, indent=2)}"
        )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        report_text = (response.text or "").strip()
    except Exception as exc:
        logger.exception("Gemini /explain (모드 %s) 호출 실패", mode)
        return (
            jsonify({"error": f"AI 보고서 생성 중 오류가 발생했습니다: {exc}"}),
            502,
        )

    logger.info("POST /explain 완료: 모드 %s (%d자)", mode, len(report_text))
    return jsonify(
        {
            "mode": mode,
            "filename": STATE.get("filename"),
            "report": report_text,
        }
    )


if __name__ == "__main__":
    logger.info("서버 시작: http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
