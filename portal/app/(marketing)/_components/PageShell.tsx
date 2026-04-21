import type { ReactNode } from "react";

type Props = {
  eyebrow: string;
  title: string;
  subtitle?: string;
  children?: ReactNode;
};

export default function PageShell({ eyebrow, title, subtitle, children }: Props) {
  return (
    <>
      <section className="page-hero">
        <div className="container">
          <div className="page-hero-inner">
            <div className="page-eyebrow">{eyebrow}</div>
            <h1 className="page-title">{title}</h1>
            {subtitle && <p className="page-subtitle">{subtitle}</p>}
          </div>
        </div>
      </section>
      {children ? (
        <section className="page-body">
          <div className="container">
            <div className="page-content">{children}</div>
          </div>
        </section>
      ) : null}
    </>
  );
}
