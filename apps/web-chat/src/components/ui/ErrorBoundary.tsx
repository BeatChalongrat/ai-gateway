"use client";

// Class-based React error boundary. Catches render-phase errors
// inside its subtree so a malformed markdown chunk (or any other
// child crash) doesn't take down the whole chat surface.

import { Component, ReactNode } from "react";

interface Props {
  /** Reset key. When this value changes the boundary clears its
   *  error state — useful when switching conversations. */
  resetKey?: string | number;
  fallback?: (error: Error, reset: () => void) => ReactNode;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div className="flex h-full items-center justify-center px-4 text-center">
        <div className="max-w-sm rounded-lg border border-status-danger/30 bg-status-danger/10 p-4">
          <h3 className="text-sm font-semibold text-text-primary">
            Something went wrong
          </h3>
          <p className="mt-1 text-[11px] text-text-muted">
            {error.message || "An unexpected error occurred while rendering."}
          </p>
          <button
            type="button"
            onClick={this.reset}
            className="focus-ring mt-3 rounded-md bg-text-primary px-3 py-1.5 text-xs font-medium text-bg-base hover:opacity-90"
          >
            Reload conversation
          </button>
        </div>
      </div>
    );
  }
}
