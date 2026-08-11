# Third-party notices

Affogato RSS Reader is MIT licensed and includes open-source Python and JavaScript
dependencies whose licenses are provided by their respective distributions.
Release images include an SPDX software bill of materials for the exact build.

Bundled runtime assets:

- MathJax 3.2.2 is distributed under the Apache License 2.0. The complete
  license text is included at `licenses/MathJax-APACHE-2.0.txt` in release
  bundles, at `/vendor/mathjax/LICENSE` in the Web application, and under
  `/usr/share/licenses/affogato-rss-reader/` in the container image.

Optional external services and data sources:

- DeepL, Google Cloud Translation, and user-configured OpenAI-compatible
  endpoints are optional translation services and remain subject to their
  respective terms, pricing, and data-processing policies.
- Google GTX is an unofficial translation endpoint. It is disabled by default,
  is not affiliated with this project, may change or throttle without notice,
  and can be selected directly or used as the automatic fallback.
- arXiv feeds may be subscribed like any other source. arXiv content and metadata
  remain subject to arXiv's terms and the rights of the original authors.

This project does not bundle feed content, PDF files, or a preconfigured source list.
