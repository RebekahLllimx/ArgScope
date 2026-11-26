# Retrieval and source versions

## Lexical retrieval

BM25 ranks passages by query-term frequency, term rarity and document length. It is useful when a question names the same technical terms as the source. Lexical retrieval can miss a relevant passage when the question uses different vocabulary.

## Vector retrieval

Character TF-IDF represents passages as weighted character n-grams and compares them using cosine similarity. It can match related spellings and morphology without downloading a neural model. Character TF-IDF is a lexical vector representation, not a semantic embedding model.

## Rank fusion

Reciprocal rank fusion combines ranked lists using the sum of one divided by a constant plus each rank. Fusion avoids treating a BM25 score and a cosine similarity as though they shared a calibrated scale. Fusion quality must still be measured against labeled retrieval questions.

## Versioned evidence

A report should pin the source versions used during retrieval. When a document changes, new searches should use the new version while old citations should continue to resolve to the original passage. A visible outdated-source indicator is more informative than silently rewriting a previous answer.

## Citation verification

Checking that a quoted passage exists in a source prevents fabricated quotations. It does not prove that the passage answers the question or that the source's claim is true. Retrieval relevance and the interpretation of evidence require separate evaluation.

## Example-library provenance

These notes were authored for the SourceLens example library. They provide a small local corpus for inspecting the application, not an external retrieval benchmark.
