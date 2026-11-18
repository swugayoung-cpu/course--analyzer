import difflib
import re
from typing import Any

import pandas as pd

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

BASE_COLUMNS = [
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

TOOL_CATALOG = {
    "quality_report": {
        "name": "quality_report",
        "description": "열별 빈칸 개수, 비정상 숫자(음수/변환실패), 비슷한 표기 후보를 점검합니다.",
        "default_columns": ["전체 열"],
    },
    "group_rate": {
        "name": "group_rate",
        "description": "그룹(단과대/이수구분/대상학년)별 행 수, 폐강 수, 폐강률을 계산합니다.",
        "default_columns": ["단과대", "폐강일자"],
    },
    "top_bottom_n": {
        "name": "top_bottom_n",
        "description": "선택한 수치 열 기준으로 상위 N개와 하위 N개 분반을 뽑습니다.",
        "default_columns": ["교과목명", "분반", "단과대", "신청_전"],
    },
    "fill_rate": {
        "name": "fill_rate",
        "description": "미폐강 강좌의 충원율(신청_전 ÷ 제한_전)을 단과대별로 계산합니다.",
        "default_columns": ["단과대", "신청_전", "제한_전", "폐강일자"],
    },
    "distribution": {
        "name": "distribution",
        "description": "분반당 수강신청 인원(신청_전) 구간별 분포를 계산합니다.",
        "default_columns": ["신청_전", "폐강일자"],
    },
    "weekday_building": {
        "name": "weekday_building",
        "description": "시간표 문자열을 쪼개 요일별·건물별 분반 수를 집계합니다.",
        "default_columns": ["시간표"],
    },
    "compare_one_vs_all": {
        "name": "compare_one_vs_all",
        "description": "특정 단과대(또는 이수구분)와 전체의 평균·중앙값을 비교합니다.",
        "default_columns": ["단과대", "신청_전", "제한_전", "학점"],
    },
}


def _closed_mask(df: pd.DataFrame) -> pd.Series:
    """폐강일자가 비어 있지 않은 행(폐강) 마스크를 반환한다."""
    return df["폐강일자"].notna() & (df["폐강일자"].astype(str).str.strip() != "")


def _make_result(
    *,
    tool_name: str,
    title: str,
    definition: str,
    used_rows: int,
    excluded_rows: int,
    excluded_reason: str,
    used_columns: list[str],
    unit: str,
    cautions: list[str],
    rows: list[dict[str, Any]],
    chart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """모든 도구가 공통으로 반환하는 표준 결과 구조."""
    return {
        "tool_name": tool_name,
        "title": title,
        "definition": definition,
        "used_rows": int(used_rows),
        "excluded_rows": int(excluded_rows),
        "excluded_reason": excluded_reason,
        "used_columns": used_columns,
        "unit": unit,
        "cautions": cautions,
        "rows": rows,
        "chart": chart,
    }


def quality_report(df: pd.DataFrame, **_: Any) -> dict[str, Any]:
    """도구 1: 빈칸 개수(열별), 비정상 숫자, 비슷한 값 후보 점검."""
    result_rows: list[dict[str, Any]] = []
    chart_labels: list[str] = []
    chart_values: list[int] = []

    for col in BASE_COLUMNS:
        if col not in df.columns:
            continue
        s = df[col]
        if col in NUMERIC_COLUMNS:
            empty_cnt = int(s.isna().sum())
        else:
            empty_cnt = int((s.isna() | (s.astype(str).str.strip() == "")).sum())
        if empty_cnt > 0:
            note = (
                "미폐강 강좌라 해당 없음일 가능성 높음"
                if col in ("폐강일자", "폐강사유")
                else "입력 누락 또는 해당 없음(파일만으로 구분 불가)"
            )
            result_rows.append(
                {
                    "점검항목": "빈칸",
                    "대상": col,
                    "건수": empty_cnt,
                    "설명": note,
                }
            )
            chart_labels.append(f"빈칸:{col}")
            chart_values.append(empty_cnt)

    for col in NUMERIC_COLUMNS:
        if col not in df.columns:
            continue
        s = df[col]
        neg_mask = s.notna() & (s < 0)
        neg_cnt = int(neg_mask.sum())
        if neg_cnt > 0:
            excel_rows = [int(i) + 3 for i in df.index[neg_mask][:10]]
            result_rows.append(
                {
                    "점검항목": "비정상 숫자(음수)",
                    "대상": col,
                    "건수": neg_cnt,
                    "설명": f"0보다 작은 값 발견 (엑셀 행: {excel_rows})",
                }
            )
            chart_labels.append(f"음수:{col}")
            chart_values.append(neg_cnt)

    for col in ("이수구분", "대표교수직급", "폐강사유"):
        if col not in df.columns:
            continue
        s = df[col].dropna().astype(str).str.strip()
        s = s[s != ""]
        counts = s.value_counts().to_dict()
        vals = list(counts.keys())
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                a, b = vals[i], vals[j]
                no_space = a.replace(" ", "") == b.replace(" ", "")
                ratio = difflib.SequenceMatcher(None, a, b).ratio()
                if no_space or ratio >= 0.75:
                    result_rows.append(
                        {
                            "점검항목": "비슷한 값 후보",
                            "대상": col,
                            "건수": int(counts[a] + counts[b]),
                            "설명": f"'{a}'({counts[a]}건) vs '{b}'({counts[b]}건)",
                        }
                    )

    return _make_result(
        tool_name="quality_report",
        title="데이터 품질 점검 리포트",
        definition="전체 데이터 행을 대상으로 열별 빈칸, 음수(0 미만), 표기가 비슷한 값 후보를 점검",
        used_rows=len(df),
        excluded_rows=0,
        excluded_reason="해당 없음 (전체 행을 대상으로 점검)",
        used_columns=BASE_COLUMNS,
        unit="건",
        cautions=[
            "폐강일자·폐강사유의 빈칸은 정상 개설 강좌라 비어 있는 '해당 없음'입니다.",
            "비슷한 값 후보는 자동 통합하지 않았으므로 필요 시 기준을 정해 합쳐야 합니다.",
        ],
        rows=result_rows,
        chart={
            "type": "bar",
            "label": "건수",
            "labels": chart_labels,
            "values": chart_values,
        },
    )


def group_rate(
    df: pd.DataFrame, group_by: str = "단과대", **_: Any
) -> dict[str, Any]:
    """도구 2: 그룹(단과대/이수구분/대상학년)별 행 수·폐강 수·폐강률 (분반 단위)."""
    allowed_groups = ("단과대", "이수구분", "대상학년")
    if group_by not in allowed_groups:
        group_by = "단과대"

    valid_mask = df[group_by].notna() & (df[group_by].astype(str).str.strip() != "")
    work = df.loc[valid_mask].copy()
    excluded = int((~valid_mask).sum())

    work["_is_closed"] = _closed_mask(work)
    grouped = (
        work.groupby(group_by, dropna=False)
        .agg(
            전체_행수=("_is_closed", "size"),
            폐강_수=("_is_closed", "sum"),
        )
        .reset_index()
    )
    grouped["폐강_수"] = grouped["폐강_수"].astype(int)
    grouped["개설유지_수"] = grouped["전체_행수"] - grouped["폐강_수"]
    grouped["폐강률_퍼센트"] = (grouped["폐강_수"] / grouped["전체_행수"] * 100).round(2)
    grouped = grouped.sort_values(
        by=["폐강률_퍼센트", "전체_행수"], ascending=[False, False]
    )

    rows = [
        {
            group_by: str(r[group_by]),
            "전체_행수": int(r["전체_행수"]),
            "개설유지_수": int(r["개설유지_수"]),
            "폐강_수": int(r["폐강_수"]),
            "폐강률(%)": float(r["폐강률_퍼센트"]),
        }
        for _, r in grouped.iterrows()
    ]

    return _make_result(
        tool_name="group_rate",
        title=f"{group_by}별 폐강률 분석",
        definition=f"폐강 = 폐강일자가 비어 있지 않은 행. 폐강률(%) = (폐강 수 ÷ {group_by} 전체 행 수) × 100",
        used_rows=len(work),
        excluded_rows=excluded,
        excluded_reason=f"{group_by} 값이 비어 있는 행 제외" if excluded > 0 else "해당 없음",
        used_columns=[group_by, "폐강일자"],
        unit="행 / %",
        cautions=[
            "같은 분반이 개설학과별로 여러 줄 나뉜 경우가 있어 행 단위 기준으로 집계했습니다.",
        ],
        rows=rows,
        chart={
            "type": "bar",
            "label": "폐강률(%)",
            "labels": [r[group_by] for r in rows[:15]],
            "values": [r["폐강률(%)"] for r in rows[:15]],
        },
    )


def top_bottom_n(
    df: pd.DataFrame,
    metric: str = "신청_전",
    n: int = 10,
    exclude_closed: bool = True,
    **_: Any,
) -> dict[str, Any]:
    """도구 3: 선택한 수치 열 기준으로 상위 N개와 하위 N개 분반을 반환."""
    allowed_metrics = ("신청_전", "제한_전", "학점")
    if metric not in allowed_metrics:
        metric = "신청_전"

    n = max(1, min(int(n), 30))

    reasons: list[str] = []
    keep_mask = pd.Series(True, index=df.index)

    if exclude_closed:
        closed = _closed_mask(df)
        if int(closed.sum()) > 0:
            reasons.append(f"폐강 {int(closed.sum())}행")
        keep_mask &= ~closed

    invalid_num = df[metric].isna() | (df[metric] < 0)
    extra_invalid = int((keep_mask & invalid_num).sum())
    if extra_invalid > 0:
        reasons.append(f"{metric} 빈칸 또는 음수 {extra_invalid}행")
    keep_mask &= ~invalid_num

    work = df.loc[keep_mask].copy()
    excluded = int((~keep_mask).sum())

    top_df = work.sort_values(by=[metric, "교과목명"], ascending=[False, True]).head(n)
    bottom_df = work.sort_values(by=[metric, "교과목명"], ascending=[True, True]).head(n)

    def _format_rows(sub: pd.DataFrame, tag: str) -> list[dict[str, Any]]:
        out = []
        for _, r in sub.iterrows():
            out.append(
                {
                    "구분": tag,
                    "교과목번호": str(r["교과목번호"]),
                    "교과목명": str(r["교과목명"]),
                    "분반": str(r["분반"]),
                    "단과대": str(r["단과대"]) if pd.notna(r["단과대"]) else "",
                    "이수구분": str(r["이수구분"]),
                    metric: float(r[metric]) if float(r[metric]) % 1 != 0 else int(r[metric]),
                    "제한_전": int(r["제한_전"]) if pd.notna(r["제한_전"]) else None,
                }
            )
        return out

    rows = _format_rows(top_df, f"상위 {n}") + _format_rows(bottom_df, f"하위 {n}")

    return _make_result(
        tool_name="top_bottom_n",
        title=f"{metric} 기준 상위·하위 {n}개 강좌",
        definition=f"선택한 수치 열({metric})을 내림차순/오름차순으로 정렬해 상위 {n}개와 하위 {n}개를 추출",
        used_rows=len(work),
        excluded_rows=excluded,
        excluded_reason=", ".join(reasons) if reasons else "해당 없음",
        used_columns=["교과목번호", "교과목명", "분반", "단과대", "이수구분", metric, "제한_전", "폐강일자"],
        unit="명" if metric != "학점" else "학점",
        cautions=[
            "같은 분반이 학과별로 나뉘어 있는 경우 각각 별도 행으로 표시됩니다.",
        ],
        rows=rows,
        chart={
            "type": "bar",
            "label": f"상위 {n} ({metric})",
            "labels": [f"{r['교과목명']}({r['분반']})" for r in rows[:n]],
            "values": [r[metric] for r in rows[:n]],
        },
    )


def fill_rate(df: pd.DataFrame, **_: Any) -> dict[str, Any]:
    """도구 4: 충원율(신청_전 ÷ 제한_전), 폐강 제외, 제한 0·빈칸 제외 및 개수 알림."""
    closed = _closed_mask(df)
    closed_cnt = int(closed.sum())

    open_df = df.loc[~closed].copy()
    invalid_limit = open_df["제한_전"].isna() | (open_df["제한_전"] <= 0)
    invalid_limit_cnt = int(invalid_limit.sum())

    invalid_apply = (~invalid_limit) & (open_df["신청_전"].isna() | (open_df["신청_전"] < 0))
    invalid_apply_cnt = int(invalid_apply.sum())

    work = open_df.loc[(~invalid_limit) & (~invalid_apply)].copy()
    work["_fill_rate"] = work["신청_전"] / work["제한_전"] * 100
    over_100_cnt = int((work["_fill_rate"] > 100).sum())

    grouped = (
        work.groupby("단과대", dropna=False)
        .agg(
            분반_행수=("_fill_rate", "size"),
            신청_합계=("신청_전", "sum"),
            제한_합계=("제한_전", "sum"),
            평균_충원율=("_fill_rate", "mean"),
            초과_100_건수=("_fill_rate", lambda s: int((s > 100).sum())),
        )
        .reset_index()
    )
    grouped["가중_충원율(%)"] = (grouped["신청_합계"] / grouped["제한_합계"] * 100).round(2)
    grouped["평균_충원율(%)"] = grouped["평균_충원율"].round(2)
    grouped = grouped.sort_values(by="가중_충원율(%)", ascending=False)

    rows = [
        {
            "단과대": str(r["단과대"]) if pd.notna(r["단과대"]) else "(미상)",
            "분반_행수": int(r["분반_행수"]),
            "신청_합계": int(r["신청_합계"]),
            "제한_합계": int(r["제한_합계"]),
            "가중_충원율(%)": float(r["가중_충원율(%)"]),
            "평균_충원율(%)": float(r["평균_충원율(%)"]),
            "100%초과_건수": int(r["초과_100_건수"]),
        }
        for _, r in grouped.iterrows()
    ]

    reasons = []
    if closed_cnt > 0:
        reasons.append(f"폐강 {closed_cnt}행")
    if invalid_limit_cnt > 0:
        reasons.append(f"제한_전 0 또는 빈칸 {invalid_limit_cnt}행(나눗셈 불가)")
    if invalid_apply_cnt > 0:
        reasons.append(f"신청_전 빈칸/음수 {invalid_apply_cnt}행")

    return _make_result(
        tool_name="fill_rate",
        title="단과대별 충원율 분석",
        definition=(
            "미폐강 강좌 중 제한_전 > 0인 행을 대상으로 충원율(%) = (신청_전 ÷ 제한_전) × 100 계산. "
            "가중 충원율은 (단과대 전체 신청 합계 ÷ 제한 합계) × 100"
        ),
        used_rows=len(work),
        excluded_rows=closed_cnt + invalid_limit_cnt + invalid_apply_cnt,
        excluded_reason=", ".join(reasons) if reasons else "해당 없음",
        used_columns=["단과대", "신청_전", "제한_전", "폐강일자"],
        unit="%",
        cautions=[
            f"신청 인원이 제한 인원보다 많아 충원율이 100%를 넘는 행이 {over_100_cnt}건 포함되어 있습니다.",
            f"제한_전이 0이거나 비어 있어 나눌 수 없는 {invalid_limit_cnt}행은 계산에서 제외했습니다.",
        ],
        rows=rows,
        chart={
            "type": "bar",
            "label": "가중 충원율(%)",
            "labels": [r["단과대"] for r in rows],
            "values": [r["가중_충원율(%)"] for r in rows],
        },
    )


def distribution(
    df: pd.DataFrame, exclude_closed: bool = True, **_: Any
) -> dict[str, Any]:
    """도구 5: 분반당 신청 인원(신청_전) 구간별 분포."""
    reasons: list[str] = []
    keep_mask = pd.Series(True, index=df.index)

    if exclude_closed:
        closed = _closed_mask(df)
        if int(closed.sum()) > 0:
            reasons.append(f"폐강 {int(closed.sum())}행")
        keep_mask &= ~closed

    invalid = df["신청_전"].isna() | (df["신청_전"] < 0)
    extra_invalid = int((keep_mask & invalid).sum())
    if extra_invalid > 0:
        reasons.append(f"신청_전 빈칸/음수 {extra_invalid}행")
    keep_mask &= ~invalid

    work = df.loc[keep_mask].copy()
    excluded = int((~keep_mask).sum())

    bins = [-1, 0, 10, 20, 30, 50, 100, float("inf")]
    labels = ["0명", "1~10명", "11~20명", "21~30명", "31~50명", "51~100명", "101명 이상"]
    bucket = pd.cut(work["신청_전"], bins=bins, labels=labels)
    counts = bucket.value_counts().reindex(labels, fill_value=0)

    total = len(work) if len(work) > 0 else 1
    rows = [
        {
            "신청인원_구간": str(label),
            "분반_행수": int(cnt),
            "비율(%)": round(int(cnt) / total * 100, 2),
        }
        for label, cnt in counts.items()
    ]

    return _make_result(
        tool_name="distribution",
        title="분반당 수강신청 인원 분포",
        definition="유효한 강좌의 신청_전 인원을 7개 구간(0명 ~ 101명 이상)으로 나누어 행 수와 비율(%)을 집계",
        used_rows=len(work),
        excluded_rows=excluded,
        excluded_reason=", ".join(reasons) if reasons else "해당 없음",
        used_columns=["신청_전", "폐강일자"],
        unit="행 / %",
        cautions=[
            "같은 분반이 개설학과별로 나뉜 행이 있어 학과별 수강인원 분포가 반영됩니다.",
        ],
        rows=rows,
        chart={
            "type": "bar",
            "label": "분반 행 수",
            "labels": [r["신청인원_구간"] for r in rows],
            "values": [r["분반_행수"] for r in rows],
        },
    )


def weekday_building(df: pd.DataFrame, **_: Any) -> dict[str, Any]:
    """도구 6: 시간표를 쪼개 요일별·건물별 분반 수 (시간표 빈 행 제외 및 개수 알림)."""
    has_sched = df["시간표"].notna() & (df["시간표"].astype(str).str.strip() != "")
    excluded = int((~has_sched).sum())
    work = df.loc[has_sched].copy()

    weekday_order = ["월", "화", "수", "목", "금", "토", "일"]
    weekday_counts = {d: 0 for d in weekday_order}
    building_counts: dict[str, int] = {}

    day_pattern = re.compile(r"[월화수목금토일]")
    building_pattern = re.compile(r"^([가-힣A-Za-z]+)")

    for raw in work["시간표"].astype(str):
        # 한 강좌에 같은 요일이 여러 강의실로 적혀도 요일별 분반 수는 1회로 집계
        found_days = set(day_pattern.findall(raw))
        for d in found_days:
            if d in weekday_counts:
                weekday_counts[d] += 1

        found_buildings: set[str] = set()
        for block in raw.split(","):
            block = block.strip()
            prefix = block.split("(")[0].strip()
            if not prefix:
                found_buildings.add("강의실미지정")
            else:
                m = building_pattern.match(prefix)
                found_buildings.add(m.group(1) if m else prefix)
        for b in found_buildings:
            building_counts[b] = building_counts.get(b, 0) + 1

    total = len(work) if len(work) > 0 else 1
    rows: list[dict[str, Any]] = []
    for d in weekday_order:
        cnt = weekday_counts[d]
        rows.append(
            {
                "구분": "요일",
                "항목": f"{d}요일",
                "분반_행수": cnt,
                "비율(%)": round(cnt / total * 100, 2),
            }
        )

    for b, cnt in sorted(building_counts.items(), key=lambda x: x[1], reverse=True)[:15]:
        rows.append(
            {
                "구분": "건물(상위 15)",
                "항목": b,
                "분반_행수": cnt,
                "비율(%)": round(cnt / total * 100, 2),
            }
        )

    return _make_result(
        tool_name="weekday_building",
        title="요일별·건물별 수업 분포",
        definition=(
            "시간표가 있는 행에서 요일(월~일)과 건물명(괄호 앞 글자 부분)을 분리해 집계. "
            "한 강좌가 여러 요일/건물을 쓰면 각각 1회씩 포함"
        ),
        used_rows=len(work),
        excluded_rows=excluded,
        excluded_reason=f"시간표가 비어 있는 {excluded}행 제외" if excluded > 0 else "해당 없음",
        used_columns=["시간표"],
        unit="행",
        cautions=[
            "한 강좌가 2개 이상 요일이나 건물에서 진행되면 각 요일·건물에 모두 카운트되므로 합계가 사용 행 수보다 클 수 있습니다.",
            f"시간표가 비어 있는 {excluded}행은 집계에서 제외했습니다.",
        ],
        rows=rows,
        chart={
            "type": "bar",
            "label": "요일별 분반 행 수",
            "labels": [f"{d}요일" for d in weekday_order],
            "values": [weekday_counts[d] for d in weekday_order],
        },
    )


def compare_one_vs_all(
    df: pd.DataFrame,
    group_col: str = "단과대",
    target_value: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """도구 7: 선택 대상(예: 특정 단과대) vs 전체 평균·중앙값 비교."""
    if group_col not in ("단과대", "이수구분", "대상학년"):
        group_col = "단과대"

    closed = _closed_mask(df)
    closed_cnt = int(closed.sum())
    work = df.loc[~closed].copy()

    # 수치 열 중 음수가 있는 행 제외
    invalid = (
        work["신청_전"].isna()
        | (work["신청_전"] < 0)
        | work["제한_전"].isna()
        | (work["제한_전"] < 0)
        | work["학점"].isna()
        | (work["학점"] < 0)
    )
    invalid_cnt = int(invalid.sum())
    work = work.loc[~invalid].copy()

    if not target_value or target_value not in set(work[group_col].dropna().astype(str)):
        target_value = str(work[group_col].dropna().astype(str).value_counts().index[0])

    sub = work.loc[work[group_col].astype(str) == target_value]
    metrics = ["신청_전", "제한_전", "학점"]

    rows = []
    for m in metrics:
        t_mean = round(float(sub[m].mean()), 2)
        a_mean = round(float(work[m].mean()), 2)
        t_med = round(float(sub[m].median()), 2)
        a_med = round(float(work[m].median()), 2)
        rows.append(
            {
                "지표": m,
                f"{target_value}_평균": t_mean,
                "전체_평균": a_mean,
                "평균_차이": round(t_mean - a_mean, 2),
                f"{target_value}_중앙값": t_med,
                "전체_중앙값": a_med,
            }
        )

    reasons = []
    if closed_cnt > 0:
        reasons.append(f"폐강 {closed_cnt}행")
    if invalid_cnt > 0:
        reasons.append(f"수치 열 빈칸/음수 {invalid_cnt}행")

    return _make_result(
        tool_name="compare_one_vs_all",
        title=f"{target_value} vs 전체 평균·중앙값 비교 ({group_col})",
        definition=f"미폐강 정상 강좌 중 '{group_col}={target_value}'({len(sub)}행)과 전체({len(work)}행)의 평균·중앙값을 비교",
        used_rows=len(work),
        excluded_rows=closed_cnt + invalid_cnt,
        excluded_reason=", ".join(reasons) if reasons else "해당 없음",
        used_columns=[group_col, "신청_전", "제한_전", "학점", "폐강일자"],
        unit="명 / 학점",
        cautions=[
            "극단적으로 큰 대형 강좌가 있으면 평균이 올라갈 수 있으므로 중앙값도 함께 확인하세요.",
        ],
        rows=rows,
        chart={
            "type": "bar",
            "label": f"{target_value} 평균 vs 전체 평균",
            "labels": [f"{r['지표']}({target_value})" for r in rows] + [f"{r['지표']}(전체)" for r in rows],
            "values": [r[f"{target_value}_평균"] for r in rows] + [r["전체_평균"] for r in rows],
        },
    )


TOOL_MAP = {
    "quality_report": quality_report,
    "group_rate": group_rate,
    "top_bottom_n": top_bottom_n,
    "fill_rate": fill_rate,
    "distribution": distribution,
    "weekday_building": weekday_building,
    "compare_one_vs_all": compare_one_vs_all,
}


def _apply_filters(
    df: pd.DataFrame, filters: list[dict[str, Any]] | None
) -> tuple[pd.DataFrame, int, str]:
    """허용된 열과 연산자만 사용해 DataFrame을 안전하게 필터링한다."""
    if not filters:
        return df, 0, ""

    mask = pd.Series(True, index=df.index)
    desc_parts: list[str] = []

    for f in filters:
        if not isinstance(f, dict):
            continue
        col = f.get("column")
        op = str(f.get("op", "==")).strip()
        val = f.get("value")
        if col not in df.columns:
            raise ValueError(f"파일에 존재하지 않는 열입니다: {col}")

        series = df[col]
        if op == "==":
            if pd.api.types.is_numeric_dtype(series):
                num_val = pd.to_numeric(val, errors="coerce")
                mask &= series == num_val
            else:
                mask &= series.fillna("").astype(str).str.strip() == str(val).strip()
        elif op == "!=":
            if pd.api.types.is_numeric_dtype(series):
                num_val = pd.to_numeric(val, errors="coerce")
                mask &= series != num_val
            else:
                mask &= series.fillna("").astype(str).str.strip() != str(val).strip()
        elif op == "contains":
            mask &= series.fillna("").astype(str).str.contains(str(val), case=False, regex=False)
        elif op in (">", ">=", "<", "<="):
            num_val = pd.to_numeric(val, errors="coerce")
            if pd.isna(num_val):
                raise ValueError(f"숫자 비교에 잘못된 값이 들어왔습니다: {val}")
            num_series = pd.to_numeric(series, errors="coerce")
            if op == ">":
                mask &= num_series > num_val
            elif op == ">=":
                mask &= num_series >= num_val
            elif op == "<":
                mask &= num_series < num_val
            else:
                mask &= num_series <= num_val
        else:
            raise ValueError(f"허용되지 않은 비교 연산자입니다: {op}")

        desc_parts.append(f"{col} {op} '{val}'")

    filtered_df = df.loc[mask].copy()
    excluded = int((~mask).sum())
    reason = f"질문 조건({', '.join(desc_parts)})에 맞지 않는 {excluded}행 제외" if excluded > 0 else ""
    return filtered_df, excluded, reason


def run_tool(df: pd.DataFrame, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """허용된 도구 목록(TOOL_MAP)에 있는 도구만 안전하게 실행한다."""
    if tool_name not in TOOL_MAP:
        raise ValueError(f"허용되지 않은 도구 이름입니다: {tool_name}")

    params = dict(params or {})
    filters = params.pop("filters", None)
    work_df, filter_excluded, filter_reason = _apply_filters(df, filters)

    if len(work_df) == 0:
        return _make_result(
            tool_name=tool_name,
            title=f"{tool_name} (조건에 맞는 행 없음)",
            definition="질문 조건으로 걸러낸 결과 해당하는 행이 0건입니다.",
            used_rows=0,
            excluded_rows=len(df),
            excluded_reason=filter_reason or "조건에 맞는 데이터 없음",
            used_columns=TOOL_CATALOG[tool_name]["default_columns"],
            unit="행",
            cautions=["조건을 조금 넓혀서 다시 질문해 보세요."],
            rows=[],
            chart=None,
        )

    fn = TOOL_MAP[tool_name]
    res = fn(work_df, **params)

    if filter_excluded > 0:
        res["excluded_rows"] = int(res.get("excluded_rows", 0)) + filter_excluded
        prev_reason = res.get("excluded_reason", "")
        if prev_reason and prev_reason != "해당 없음":
            res["excluded_reason"] = f"{filter_reason}, {prev_reason}"
        else:
            res["excluded_reason"] = filter_reason
        if filter_reason:
            res["definition"] = f"[필터 적용: {filter_reason}] " + res["definition"]

    return res
