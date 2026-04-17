interface Props {
  message: string;
  onRetry?: () => void;
}

export default function ErrorState({ message, onRetry }: Props) {
  return (
    <div className="sran-error-state">
      <div className="sran-error-state-icon">!</div>
      <p className="sran-error-state-message">{message}</p>
      {onRetry && (
        <button className="sran-btn sran-btn-secondary sran-btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
