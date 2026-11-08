# Master Dataset audit — 25 October 2026

A: retain partial extraction, improve verified defects; B only for individually retrieval-ready papers

Schema + canonical identity + PDF checksum + headings + paragraph page ranges + per-page word coverage >= 0.80 on text-rich pages. Retrieval readiness is provisional usability, not scientific completeness. QA is independent.

Selection: live PostgreSQL paper_sources (read-only)

## Summary

- selected_papers: 150
- audited_papers: 150
- unique_canonical_papers: 150
- duplicate_canonical_papers: 0
- missing_selected_papers: []
- imported_arxiv_papers: 150
- valid_pdf_papers: 150
- usable_full_text_papers: 147
- structured_section_papers: 149
- papers_with_figures: 146
- papers_with_tables: 137
- papers_with_references: 148
- papers_with_qas: 0
- papers_with_verified_qa_evidence: 0
- retrieval_ready_papers: 147
- qa_training_ready_papers: 0
- fully_complete_papers: 0
- issue_counts: {'object_structure_partial': 149, 'table_caption_without_cells': 74, 'bibliography_partial_fragments': 148, 'duplicate_long_paragraphs': 36, 'pdf_caption_candidates_missing_from_objects': 5, 'unmapped_scientific_glyphs': 17, 'excessive_unmapped_glyphs': 1, 'bibliography_not_detected_absence_unverified': 2, 'section_headings_unverified': 1, 'low_page_word_coverage': 1}

## Verification boundaries

- Repository: all selected exported documents validated and compared page by page to local PDF text. Coverage checks cannot prove equations, reading order or table semantics.
- PostgreSQL: verified_read_only. Current results are in the JSON report; historical documentation is not live evidence.
- Export-only differences are recorded in postgres.consistency. No production writes occur in this audit.
- Visual sample review is separate in manual_pdf_review.md. Unknown figure/table absence is never counted as a failure merely because an array is empty.
- References count fragments, not verified bibliography entries. No generated QA or training is performed.

## Per-paper decision

| ID | Retrieval | QA train | Issues |
| --- | --- | --- | --- |
| arxiv:1410.3916 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1503.00759 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1503.02917 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1503.08895 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1503.09137 | True | False | duplicate_long_paragraphs:2; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1504.07678 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1506.01094 | True | False | pdf_caption_candidates_missing_from_objects:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1508.00715 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1508.01755 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1508.02593 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1510.04935 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1510.05911 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1511.00060 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1601.03778 | True | False | unmapped_scientific_glyphs:6; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1604.04795 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1605.06065 | True | False | duplicate_long_paragraphs:1; unmapped_scientific_glyphs:10; object_structure_partial; bibliography_partial_fragments |
| arxiv:1606.02355 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1607.00992 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1707.01961 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1802.00510 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1805.09354 | True | False | unmapped_scientific_glyphs:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1806.03822 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1808.09602 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1810.04805 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1904.08375 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1904.08936 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1905.08957 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:1906.00067 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1910.09760 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1910.10683 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:1911.07673 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2002.08909 | True | False | unmapped_scientific_glyphs:3; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2003.00719 | True | False | duplicate_long_paragraphs:4; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2004.04906 | True | False | unmapped_scientific_glyphs:3; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2004.14974 | True | False | duplicate_long_paragraphs:1; object_structure_partial; bibliography_partial_fragments |
| arxiv:2005.11401 | True | False | duplicate_long_paragraphs:2; object_structure_partial; bibliography_partial_fragments |
| arxiv:2007.00808 | True | False | duplicate_long_paragraphs:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2007.01282 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2010.08191 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2011.01103 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2101.00294 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2101.02235 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2104.05832 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2104.07567 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2104.12622 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2105.00811 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2105.03011 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2105.07624 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2105.14517 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2106.12875 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2108.06027 | True | False | unmapped_scientific_glyphs:2; object_structure_partial; bibliography_partial_fragments |
| arxiv:2108.08105 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2108.13934 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2112.02342 | True | False | unmapped_scientific_glyphs:2; object_structure_partial; bibliography_partial_fragments |
| arxiv:2201.00464 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2205.00970 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2205.01331 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2208.00511 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2208.07670 | True | False | unmapped_scientific_glyphs:2; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2210.03629 | True | False | duplicate_long_paragraphs:5; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2211.04142 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2303.11366 | True | False | duplicate_long_paragraphs:2; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2303.17580 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2304.03442 | True | False | duplicate_long_paragraphs:10; object_structure_partial; bibliography_partial_fragments |
| arxiv:2304.13343 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2305.10250 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2305.13197 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2306.00947 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2306.07174 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2308.11432 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2309.02427 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2309.07870 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2310.06626 | True | False | unmapped_scientific_glyphs:2; object_structure_partial; bibliography_partial_fragments |
| arxiv:2312.16821 | True | False | duplicate_long_paragraphs:3; object_structure_partial; bibliography_partial_fragments |
| arxiv:2401.18059 | True | False | duplicate_long_paragraphs:1; object_structure_partial; bibliography_partial_fragments |
| arxiv:2402.01622 | True | False | duplicate_long_paragraphs:1; object_structure_partial; bibliography_partial_fragments |
| arxiv:2402.02764 | False | False | duplicate_long_paragraphs:4; unmapped_scientific_glyphs:176; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments; excessive_unmapped_glyphs |
| arxiv:2402.06664 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2402.11035 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2402.14034 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2402.14672 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2402.17753 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2403.01469 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2403.08715 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2403.16843 | True | False | duplicate_long_paragraphs:24; object_structure_partial; bibliography_partial_fragments |
| arxiv:2404.16130 | True | False | duplicate_long_paragraphs:4; object_structure_partial; bibliography_partial_fragments |
| arxiv:2405.01535 | True | False | duplicate_long_paragraphs:2; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2405.13008 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2405.13009 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2406.12639 | True | False | pdf_caption_candidates_missing_from_objects:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2406.14562 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2407.01231 | True | False | duplicate_long_paragraphs:15; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2407.01489 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2408.06458 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2411.04075 | True | False | duplicate_long_paragraphs:1; pdf_caption_candidates_missing_from_objects:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2411.08432 | True | False | pdf_caption_candidates_missing_from_objects:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2412.21154 | True | False | duplicate_long_paragraphs:1; object_structure_partial; bibliography_partial_fragments |
| arxiv:2502.00306 | True | False | duplicate_long_paragraphs:5; unmapped_scientific_glyphs:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2502.21024 | True | False | duplicate_long_paragraphs:4; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2503.11733 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2504.00727 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2504.08756 | True | False | unmapped_scientific_glyphs:4; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2504.09723 | True | False | duplicate_long_paragraphs:8; object_structure_partial; bibliography_partial_fragments |
| arxiv:2504.15689 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2505.14279 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2505.19288 | True | False | duplicate_long_paragraphs:7; object_structure_partial; bibliography_partial_fragments |
| arxiv:2506.19967 | True | False | duplicate_long_paragraphs:8; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2508.09124 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2508.13743 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2508.16439 | True | False | duplicate_long_paragraphs:8; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2508.16629 | True | False | duplicate_long_paragraphs:1; object_structure_partial; bibliography_partial_fragments |
| arxiv:2508.18929 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2509.14477 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2509.25459 | True | False | duplicate_long_paragraphs:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2510.02209 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2510.02827 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2510.08255 | True | False | duplicate_long_paragraphs:9; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2510.15682 | True | False | duplicate_long_paragraphs:3; object_structure_partial; bibliography_partial_fragments |
| arxiv:2510.22344 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2512.13706 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2601.01885 | True | False | unmapped_scientific_glyphs:3; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2601.03258 | False | False | object_structure_partial; bibliography_not_detected_absence_unverified; section_headings_unverified |
| arxiv:2601.05264 | True | False | duplicate_long_paragraphs:13; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2601.06973 | True | False | duplicate_long_paragraphs:7; object_structure_partial; bibliography_partial_fragments |
| arxiv:2601.11969 | True | False | duplicate_long_paragraphs:5; pdf_caption_candidates_missing_from_objects:1; object_structure_partial; bibliography_partial_fragments |
| arxiv:2602.00279 | False | False | low_page_word_coverage:26; duplicate_long_paragraphs:3; object_structure_partial; bibliography_partial_fragments |
| arxiv:2602.02774 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2602.07739 | True | False | unmapped_scientific_glyphs:3; object_structure_partial; bibliography_partial_fragments |
| arxiv:2602.23320 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2603.14828 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2603.16877 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2603.24925 | True | False | duplicate_long_paragraphs:2; object_structure_partial; bibliography_partial_fragments |
| arxiv:2604.19899 | True | False | duplicate_long_paragraphs:4; object_structure_partial; bibliography_partial_fragments |
| arxiv:2604.20006 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2605.01664 | True | False | bibliography_partial_fragments |
| arxiv:2605.04495 | True | False | duplicate_long_paragraphs:1; object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2605.17007 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2605.29653 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2605.30283 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2606.01070 | True | False | unmapped_scientific_glyphs:1; object_structure_partial; bibliography_partial_fragments |
| arxiv:2606.10694 | True | False | unmapped_scientific_glyphs:2; object_structure_partial; bibliography_partial_fragments |
| arxiv:2606.25161 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2607.05690 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2608.00009 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2608.07429 | True | False | object_structure_partial; table_caption_without_cells; bibliography_not_detected_absence_unverified |
| arxiv:2608.11248 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2609.38473 | True | False | object_structure_partial; bibliography_partial_fragments |
| arxiv:2610.00010 | True | False | unmapped_scientific_glyphs:5; object_structure_partial; bibliography_partial_fragments |
| arxiv:2610.15837 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
| arxiv:2610.31303 | True | False | object_structure_partial; table_caption_without_cells; bibliography_partial_fragments |
