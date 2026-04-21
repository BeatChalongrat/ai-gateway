export function HeroBackground() {
  return (
    <div
      aria-hidden
      className="absolute inset-0 -z-10 overflow-hidden pointer-events-none"
      style={{
        background: "linear-gradient(180deg, #dfe0ef 0%, #d4d7ec 100%)",
      }}
    >
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(rgba(15,23,42,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(15,23,42,0.05) 1px, transparent 1px)",
          backgroundSize: "40px 40px",
          WebkitMaskImage:
            "radial-gradient(ellipse 70% 60% at 50% 50%, #000 20%, transparent 90%)",
          maskImage:
            "radial-gradient(ellipse 70% 60% at 50% 50%, #000 20%, transparent 90%)",
        }}
      />

      <div
        className="absolute rounded-full blur-3xl animate-aurora-1"
        style={{
          background: "#a5b4fc",
          top: "-80px",
          left: "-120px",
          width: 480,
          height: 480,
          opacity: 0.65,
        }}
      />
      <div
        className="absolute rounded-full blur-3xl animate-aurora-2"
        style={{
          background: "#c4b5fd",
          bottom: "-80px",
          right: "-150px",
          width: 500,
          height: 500,
          opacity: 0.55,
        }}
      />
      <div
        className="absolute rounded-full blur-3xl animate-aurora-3"
        style={{
          background: "#bae6fd",
          top: "40%",
          left: "30%",
          width: 280,
          height: 280,
          opacity: 0.3,
        }}
      />
    </div>
  );
}
