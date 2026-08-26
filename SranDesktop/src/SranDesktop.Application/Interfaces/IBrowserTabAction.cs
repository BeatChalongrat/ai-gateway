namespace SranDesktop.Application.Interfaces;

/// <summary>
/// FIX6-addendum — the DATA that decides the per-site gate's family. A browser
/// tool that ACTS ON the current/target tab (click, type, read, find, screenshot,
/// activate/close a tab) implements this, and therefore requires a host-authorized
/// ORIGIN before it may act. The sidecar's gate keys on <c>tool is IBrowserTabAction</c>
/// — the same shape as PathGuard keying on <c>tool.Category</c> — never on a string
/// match of the tool name.
///
/// Deliberately NOT implemented by: <c>browser_navigate</c> / <c>open_url</c> (they
/// gate on their DESTINATION url, not the current tab) and <c>browser_list_tabs</c>
/// (the bootstrap enumerator that DISCOVERS which sites are open). Adding a new
/// tab-acting browser tool without this marker leaves it ungated — which is why the
/// marker, and the test that pins the family, live next to the tools.
/// </summary>
public interface IBrowserTabAction
{
}
