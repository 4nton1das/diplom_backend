# modules/llm/structured.py

import json
import re
from typing import Optional, Any

from pydantic import BaseModel, Field, ValidationError


class StructuredPoint(BaseModel):
    time: Optional[float] = None
    text: str


class StructuredSection(BaseModel):
    id: Optional[str] = None
    title: str
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    summary: str
    points: list[StructuredPoint] = Field(default_factory=list)


class StructuredTerm(BaseModel):
    term: str
    definition: str
    time: Optional[float] = None


class StructuredQuestion(BaseModel):
    question: str
    answer: str
    time: Optional[float] = None


class StructuredChunkSummary(BaseModel):
    chunk_index: int
    start_time: float
    end_time: float
    sections: list[StructuredSection] = Field(default_factory=list)
    terms: list[StructuredTerm] = Field(default_factory=list)
    questions: list[StructuredQuestion] = Field(default_factory=list)


class StructuredSummary(BaseModel):
    title: str
    overview: str
    sections: list[StructuredSection] = Field(default_factory=list)
    terms: list[StructuredTerm] = Field(default_factory=list)
    questions: list[StructuredQuestion] = Field(default_factory=list)


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Извлекает JSON-объект из ответа LLM.

    Поддерживает:
    - чистый JSON;
    - ```json ... ```;
    - лишний текст до/после JSON;
    - trailing commas;
    - незакавыченные английские ключи вида title: "...".
    """
    if not text:
        raise ValueError("LLM returned empty response")

    cleaned = strip_code_fences(text)

    candidates = [
        cleaned,
        extract_balanced_json_object(cleaned),
    ]

    last_error: Exception | None = None

    for candidate in candidates:
        if not candidate:
            continue

        for prepared in [
            candidate,
            repair_common_json_issues(candidate),
        ]:
            try:
                parsed = json.loads(prepared)

                if not isinstance(parsed, dict):
                    raise ValueError("LLM JSON response must be an object")

                return parsed

            except Exception as e:
                last_error = e

    raise ValueError(f"Invalid JSON from LLM: {last_error}")


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()

    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()

    return cleaned.strip()


def extract_balanced_json_object(text: str) -> str | None:
    """
    Ищет первый сбалансированный JSON object.
    Надёжнее, чем regex {.*}, потому что учитывает строки.
    """
    start = text.find("{")

    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        char = text[i]

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1

        elif char == "}":
            depth -= 1

            if depth == 0:
                return text[start:i + 1]

    return None


def repair_common_json_issues(text: str) -> str:
    repaired = text.strip()

    # Убираем trailing commas:
    # {"a": 1,} / [1,2,]
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

    # Python-like values → JSON values
    repaired = re.sub(r"\bNone\b", "null", repaired)
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)

    # Незакавченные английские ключи:
    # { title: "..." } → { "title": "..." }
    repaired = re.sub(
        r'([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)',
        r'\1"\2"\3',
        repaired,
    )

    return repaired


def parse_structured_chunk_summary(
    text: str,
    chunk_index: int,
    chunk_start: float,
    chunk_end: float,
) -> StructuredChunkSummary:
    raw = extract_json_object(text)

    raw["chunk_index"] = chunk_index
    raw["start_time"] = chunk_start
    raw["end_time"] = chunk_end

    try:
        parsed = StructuredChunkSummary.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid structured chunk schema: {e}") from e

    return normalize_chunk_times(parsed, chunk_start, chunk_end)


def normalize_chunk_times(
    chunk: StructuredChunkSummary,
    chunk_start: float,
    chunk_end: float,
) -> StructuredChunkSummary:
    """
    LLM не имеет права выводить время за пределами своего чанка.
    Все значения обрезаем по границам чанка.
    """
    for section in chunk.sections:
        section.start_time = clamp_time(section.start_time, chunk_start, chunk_end)
        section.end_time = clamp_time(section.end_time, chunk_start, chunk_end)

        if section.start_time is None:
            section.start_time = chunk_start

        if section.end_time is None:
            section.end_time = chunk_end

        if section.end_time < section.start_time:
            section.end_time = section.start_time

        for point in section.points:
            point.time = clamp_time(point.time, chunk_start, chunk_end)

    for term in chunk.terms:
        term.time = clamp_time(term.time, chunk_start, chunk_end)

    for question in chunk.questions:
        question.time = clamp_time(question.time, chunk_start, chunk_end)

    return chunk


def clamp_time(value: Optional[float], start: float, end: float) -> Optional[float]:
    if value is None:
        return None

    value = float(value)

    if value < start:
        return start

    if value > end:
        return end

    return value


def merge_chunk_summaries(
    chunks: list[StructuredChunkSummary],
    title: Optional[str] = None,
    overview: Optional[str] = None,
) -> StructuredSummary:
    sections: list[StructuredSection] = []
    terms: list[StructuredTerm] = []
    questions: list[StructuredQuestion] = []

    for chunk in chunks:
        for section in chunk.sections:
            section.id = f"section_{len(sections) + 1}"
            sections.append(section)

        terms.extend(chunk.terms)
        questions.extend(chunk.questions)

    terms = deduplicate_terms(terms)
    questions = questions[:20]

    if not title:
        title = build_fallback_title(sections)

    if not overview:
        overview = build_fallback_overview(sections)

    return StructuredSummary(
        title=title,
        overview=overview,
        sections=sections,
        terms=terms,
        questions=questions,
    )


def deduplicate_terms(terms: list[StructuredTerm]) -> list[StructuredTerm]:
    result: list[StructuredTerm] = []
    seen: set[str] = set()

    for term in terms:
        key = normalize_key(term.term)

        if not key or key in seen:
            continue

        seen.add(key)
        result.append(term)

        if len(result) >= 40:
            break

    return result


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def build_fallback_title(sections: list[StructuredSection]) -> str:
    if sections:
        return sections[0].title

    return "Интерактивный конспект"


def build_fallback_overview(sections: list[StructuredSection]) -> str:
    if not sections:
        return "Материал был автоматически обработан и структурирован."

    first = sections[:3]
    joined = " ".join(section.summary for section in first if section.summary)

    return joined[:800] or "Материал был автоматически обработан и структурирован."


def structured_chunk_to_markdown(chunk: StructuredChunkSummary) -> str:
    parts: list[str] = []

    parts.append(
        f"## Фрагмент {chunk.chunk_index}: {format_seconds(chunk.start_time)}–{format_seconds(chunk.end_time)}"
    )
    parts.append("")

    for section in chunk.sections:
        parts.append(f"### {section.title}")
        parts.append("")
        parts.append(section.summary)
        parts.append("")

        for point in section.points:
            if point.time is not None:
                parts.append(f"- `{format_seconds(point.time)}` — {point.text}")
            else:
                parts.append(f"- {point.text}")

        parts.append("")

    return "\n".join(parts).strip()


def structured_summary_to_markdown(summary: StructuredSummary) -> str:
    parts: list[str] = []

    parts.append(f"# {summary.title}")
    parts.append("")

    parts.append("## Краткое содержание")
    parts.append("")
    parts.append(summary.overview.strip())
    parts.append("")

    if summary.sections:
        parts.append("## Подробный конспект")
        parts.append("")

        for section in summary.sections:
            time_range = format_time_range(section.start_time, section.end_time)

            if time_range:
                parts.append(f"### {section.title} `{time_range}`")
            else:
                parts.append(f"### {section.title}")

            parts.append("")
            parts.append(section.summary.strip())
            parts.append("")

            if section.points:
                parts.append("**Ключевые моменты:**")
                parts.append("")

                for point in section.points:
                    if point.time is not None:
                        parts.append(f"- `{format_seconds(point.time)}` — {point.text}")
                    else:
                        parts.append(f"- {point.text}")

                parts.append("")

    if summary.terms:
        parts.append("## Ключевые понятия")
        parts.append("")

        for term in summary.terms:
            if term.time is not None:
                parts.append(
                    f"- **{term.term}** `{format_seconds(term.time)}` — {term.definition}"
                )
            else:
                parts.append(f"- **{term.term}** — {term.definition}")

        parts.append("")

    if summary.questions:
        parts.append("## Вопросы для самопроверки")
        parts.append("")

        for index, question in enumerate(summary.questions, start=1):
            if question.time is not None:
                parts.append(
                    f"{index}. **{question.question}** `{format_seconds(question.time)}`"
                )
            else:
                parts.append(f"{index}. **{question.question}**")

            parts.append("")
            parts.append(f"   {question.answer}")
            parts.append("")

    parts.append("## Итог")
    parts.append("")
    parts.append("Материал структурирован по смысловым разделам и связан с временными метками исходной записи.")

    return "\n".join(parts).strip()


def format_time_range(start: Optional[float], end: Optional[float]) -> str:
    if start is None and end is None:
        return ""

    if start is not None and end is not None:
        return f"{format_seconds(start)}–{format_seconds(end)}"

    if start is not None:
        return f"с {format_seconds(start)}"

    return f"до {format_seconds(end)}"


def format_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "00:00"

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"
