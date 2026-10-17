// Test-only Next font fixture. Does not validate delivery or the real font files.
// Opt in via NEXT_FONT_GOOGLE_MOCKED_RESPONSES; never use for a deployment.
module.exports = new Proxy({}, {
  get: (_target, url) => `@font-face {
    font-family: '${String(url).includes("JetBrains") ? "JetBrains Mono" : "Figtree"}';
    src: local('Arial'); font-style: normal; font-weight: 100 900; font-display: swap;
  }`,
});
