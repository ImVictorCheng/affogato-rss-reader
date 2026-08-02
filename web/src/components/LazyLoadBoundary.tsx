import { Component, type ReactNode } from "react";

type LazyLoadBoundaryProps = {
  children: ReactNode;
  title: string;
  message: string;
  reloadLabel: string;
  backLabel: string;
  onReload: () => void;
  onBack: () => void;
};

type LazyLoadBoundaryState = {
  failed: boolean;
};

/** Keeps an optional, code-split workspace failure inside that workspace. */
export class LazyLoadBoundary extends Component<LazyLoadBoundaryProps, LazyLoadBoundaryState> {
  state: LazyLoadBoundaryState = { failed: false };

  static getDerivedStateFromError(): LazyLoadBoundaryState {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <main className="brief-workspace brief-workspace--load-error">
        <section className="workspace-load-error" role="alert">
          <span className="workspace-load-error__symbol" aria-hidden="true">!</span>
          <div className="workspace-load-error__copy">
            <h1>{this.props.title}</h1>
            <p>{this.props.message}</p>
          </div>
          <div className="workspace-load-error__actions">
            <button type="button" className="button button--primary" onClick={this.props.onReload}>
              {this.props.reloadLabel}
            </button>
            <button type="button" className="text-button" onClick={this.props.onBack}>
              {this.props.backLabel}
            </button>
          </div>
        </section>
      </main>
    );
  }
}
