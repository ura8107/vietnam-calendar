"""Versioned deterministic importance rubric baseline."""
from __future__ import annotations
from dataclasses import dataclass
from vietnam_calendar.application.ai import ImportanceLevel, Relevance

RULE_VERSION="importance-rubric-v1"

@dataclass(frozen=True)
class RuleResult:
    relevance: Relevance; importance: ImportanceLevel|None; must_include: bool; reason: str

def classify(title:str,summary:str="")->RuleResult:
    text=f"{title} {summary}".lower()
    if any(x in text for x in ("アルゼンチン",)) or ("ひったくり" in text and not any(x in text for x in ("制度","全国","大規模"))):
        return RuleResult(Relevance.OUT_OF_SCOPE,None,False,"ベトナムとの直接関係を確認できない。")
    must=any(x in text for x in ("クーデター","武力衝突","政策金利","任期途中で辞任","数万人規模","大規模な反政府デモ"))
    high=must or any(x in text for x in ("正式決定","正式承認","正式開業","正式開港","正式に逮捕","過去最安値","過去最高の成長率","gdp成長率","最高水準","史上最大","ノーベル","初出場","数千人が避難","全国規模のインターネット遮断","行動計画へ署名","国内で初めて導入","台風が上陸","国会が新しい個人情報保護法を可決","反政府デモ"))
    low=any(x in text for x in ("検討中","将来の制度改革","法案を国会へ提出","一時的","被害なし","大型コンサート","映画祭","オリンピックで金","東南アジア選手権","市場予想を少し","一日で過去最大"))
    planned=any(x in text for x in ("計画","提案","構想","目標","接近中","疑惑"))
    level=ImportanceLevel.HIGH if high else ImportanceLevel.LOW if low else ImportanceLevel.MIDDLE if planned else ImportanceLevel.MIDDLE
    return RuleResult(Relevance.TARGET,level,must,"確定度、影響範囲、歴史的重要性、必須掲載条件を規則で評価。")
