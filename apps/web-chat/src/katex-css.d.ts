// KaTeX ships its stylesheet as a plain .css file we load via a
// dynamic import() for side effects (see ChatMarkdown's lazy math
// loader). TypeScript needs a module declaration for the bare path.
declare module "katex/dist/katex.min.css";
