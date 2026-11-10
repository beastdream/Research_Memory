"""Unit tests for the Information Bottleneck Noise Filter module."""
import pytest
from app.retrieval.ib_noise_filter import InformationBottleneckFilter, FilteredEvidence


@pytest.fixture
def sample_passages():
    return [
        {
            "chunk_id": "chunk_relevant_1",
            "title": "Retrieval-Augmented Generation for Scientific QA",
            "section_title": "Methodology",
            "text": (
                "Retrieval-Augmented Generation (RAG) significantly reduces factual hallucinations in Large Language Models. "
                "By integrating external parametric knowledge from ScienceDirect articles, the system grounds generated claims. "
                "The primary mechanism relies on dense vector retrieval coupled with cross-encoder reranking. "
                "Furthermore, our empirical evaluations demonstrate a 35% improvement in factual faithfulness on benchmark datasets."
            ),
        },
        {
            "chunk_id": "chunk_relevant_2",
            "title": "Retrieval-Augmented Generation for Scientific QA",
            "section_title": "Results",
            "text": (
                "Our empirical evaluations demonstrate a 35% improvement in factual faithfulness on benchmark datasets. "
                "This confirms that grounding LLMs with scientific evidence prevents catastrophic hallucination. "
                "The experiments were conducted using NVIDIA GPUs over 50 epochs."
            ),
        },
        {
            "chunk_id": "chunk_noise_distractor",
            "title": "Agricultural Soil Analysis in Sub-Tropical Regions",
            "section_title": "Soil Composition",
            "text": (
                "Soil moisture levels were collected using capacitive sensors installed across 15 agricultural plots. "
                "Nitrogen and potassium levels varied significantly depending on the depth of the soil layer. "
                "Rainfall patterns in late autumn accelerated nutrient depletion in sandy substrates."
            ),
        },
    ]


def test_sentence_splitting():
    filter_mod = InformationBottleneckFilter()
    text = "Large Language Models (LLMs) hallucinate. However, RAG mitigates this issue. Fig. 1 shows the pipeline."
    sentences = filter_mod.split_sentences(text)
    assert len(sentences) >= 2
    assert any("hallucinate" in s for s in sentences)


def test_coarse_grained_passage_filtering(sample_passages):
    filter_mod = InformationBottleneckFilter()
    query = "How does Retrieval-Augmented Generation mitigate hallucinations in Large Language Models?"

    filtered = filter_mod.filter_passages(query, sample_passages, top_k=5)

    # Check that relevant passages are ranked higher than soil analysis noise
    assert len(filtered) >= 1
    top_chunk_ids = [p["chunk_id"] for p in filtered]
    assert "chunk_relevant_1" in top_chunk_ids
    assert top_chunk_ids[0] != "chunk_noise_distractor"


def test_fine_grained_ib_evidence_extraction(sample_passages):
    filter_mod = InformationBottleneckFilter(beta=0.4, redundancy_lambda=0.6, max_context_tokens=150)
    query = "How does RAG reduce hallucinations in LLMs?"

    result = filter_mod.extract_compact_evidence(query, sample_passages, max_tokens=120)

    assert isinstance(result, FilteredEvidence)
    assert result.original_passages_count == 3
    assert result.compressed_token_count > 0
    assert result.compressed_token_count < result.original_token_count
    # Should compress at least 40% of original tokens
    assert result.compression_ratio > 0.40

    # Ensure selected sentences focus on hallucination and RAG
    compact_text = result.compact_context.lower()
    assert "hallucination" in compact_text or "rag" in compact_text
    assert "soil" not in compact_text  # Distractor pruned!


def test_redundancy_penalty_avoids_duplicate_sentences(sample_passages):
    # chunk_relevant_1 and chunk_relevant_2 share the exact sentence:
    # "Our empirical evaluations demonstrate a 35% improvement in factual faithfulness on benchmark datasets."
    filter_mod = InformationBottleneckFilter(redundancy_lambda=0.9, max_context_tokens=100)
    query = "What is the empirical improvement in factual faithfulness?"

    result = filter_mod.extract_compact_evidence(query, sample_passages)

    # Count how many times the sentence appears
    selected_texts = [s["sentence"] for s in result.selected_sentences]
    repeated_sentence = "Our empirical evaluations demonstrate a 35% improvement in factual faithfulness on benchmark datasets."
    count = selected_texts.count(repeated_sentence)
    # Redundancy penalty must ensure it is included at most ONCE
    assert count <= 1

