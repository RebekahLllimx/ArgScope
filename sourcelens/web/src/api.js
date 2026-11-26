export const METHODS = {
  hybrid: "Hybrid · BM25 + TF-IDF",
  bm25: "BM25 · keywords",
  tfidf: "TF-IDF · character vectors",
};

export async function api(path, options = {}) {
  const response = await fetch("/api" + path, options);
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : "Check the submitted fields and try again.",
    );
  }
  return response.json();
}
export const json = (value) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
