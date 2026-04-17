export default function LoadingState({ message = "Loading..." }: { message?: string }) {
  return (
    <div className="sran-empty">
      <div
        style={{
          width: "1.5rem",
          height: "1.5rem",
          border: "2px solid var(--color-border-default)",
          borderTopColor: "var(--color-sran-700)",
          borderRadius: "50%",
          animation: "spin 0.6s linear infinite",
          margin: "0 auto 0.75rem",
        }}
      />
      <p>{message}</p>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
