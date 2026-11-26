"""Short original-paper tasks; candidate interpretations are not gold answers."""


def research_examples():
    quine = "It is some such line of thought that leads philosophers like McX to impute being where they might otherwise be quite content to recognize that there is nothing. Thus, take Pegasus. If Pegasus were not, McX argues, we should not be talking about anything when we use the word; therefore it would be nonsense to say even that Pegasus is not. Thinking to show thus that the denial of Pegasus cannot be coherently maintained, he concludes that Pegasus is."
    chalmers = "Then we can put the argument for an intelligence explosion as follows:\n1. There will be AI+.\n2. If there is AI+, there will be AI++.\n3. There will be AI++.\nHere, premise 1 needs independent support (on which more soon), but is often taken to be plausible. Premise 2 is the key claim of the intelligence explosion, and is supported by Good’s reasoning set out above. The conclusion says that there will be superintelligence."
    return [
        {
            "id": "quine-description-first-order",
            "label": "Quine — negative descriptions (first-order, original phrases)",
            "request": {
                "question": "Check Quine's Russellian analysis of a negative description: does failure of unique authorship mean nobody wrote Waverley, or allow multiple authors? Formalize and check both the proposed equivalence and the tempting zero-author shortcut.",
                "notes": "Two exact source phrases, separated by an editorial newline, from the same paragraph. W(x) means x wrote Waverley. Focus the main reading on one direction: P1 = ~(exists x. (W(x) & forall y. (W(y) -> y = x))); C = (forall x. ~W(x)) | (exists x. (W(x) & exists y. (W(y) & ~(x = y)))). P1 is a hypothetical supposition, not Quine asserting the historically false denial. C is the conditional consequence under that supposition. Use semantics classical-first-order-v1 in formal_mapping, formulas keyed by existing node IDs, representation first_order with rationale. No constants or function symbols; all variables bound. This is guided reconstruction of a local equivalence, not a proof of Quine's full view of meaningfulness. Do not claim an individual named author exists merely because the phrase is used.",
                "sources": [
                    {
                        "id": "main",
                        "title": "Quine, On What There Is: negative Waverley description",
                        "version": "Colorado p.4; two exact phrases joined by editorial newline",
                        "kind": "original_excerpt",
                        "text": "The author of Waverley is not\nEither each thing failed to write Waverley or two or more things wrote Waverley",
                        "reference_url": "https://rintintin.colorado.edu/~vancecd/phil375/Quine.pdf",
                    }
                ],
            },
        },
        {
            "id": "quine-mcx-original",
            "label": "Quine — reconstruct the reported McX inference (original)",
            "request": {
                "question": "Reconstruct the McX inference that Quine reports, distinguishing McX's assumptions from Quine's endorsement. What stated or implicit premise is needed to infer that Pegasus is? Attempt a scoped propositional check.",
                "notes": "Use short exact English quotes and concise explanations. A candidate abstraction may use p = Pegasus is, and m = the relevant Pegasus discourse is meaningful. Explain any move between talking-about and meaningfulness; these are not automatically identical. Mark any added premise as proposed_assumption with rationale. A propositional_abstraction qualification may delimit a local inference without claiming to solve reference or ontology. Do not force validity or attribute McX's conclusion to Quine. Preserve genuinely unencoded limitations if necessary.",
                "sources": [
                    {
                        "id": "main",
                        "title": "Quine, On What There Is (1948), reported McX argument",
                        "version": "Colorado teaching transcription p.1; whitespace normalized",
                        "kind": "original_excerpt",
                        "text": quine,
                        "reference_url": "https://rintintin.colorado.edu/~vancecd/phil375/Quine.pdf",
                    }
                ],
            },
        },
        {
            "id": "chalmers-singularity-original",
            "label": "Chalmers — intelligence explosion (§2, original)",
            "request": {
                "question": "Reconstruct the explicitly numbered argument in section 2. Does its conclusion follow from its two stated premises, and does that establish the truth of those premises?",
                "notes": "Check the author's displayed three-line inference, not the later qualified temporal argument. p = There will be AI+; q = There will be AI++. Use P1=p, P2=p -> q, C=q if faithful. State atom meanings and the limited propositional scope. Do not invent empirical support for either premise. A scope qualification should use representation propositional_abstraction for this explicitly propositional inference. Human review is still required.",
                "sources": [
                    {
                        "id": "main",
                        "title": "Chalmers, The Singularity: A Philosophical Analysis, §2",
                        "version": "course PDF p.5 / printed p.11; line wrapping normalized",
                        "kind": "original_excerpt",
                        "text": chalmers,
                        "reference_url": "https://www.consc.net/papers/singularity.pdf",
                    }
                ],
            },
        },
    ]
