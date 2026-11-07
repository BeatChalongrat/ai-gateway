import type { ReactNode } from "react";

type Props = {
  visual: ReactNode;
  form: ReactNode;
};

export default function AuthSplitLayout({ visual, form }: Props) {
  return (
    <div className="auth-root">
      <div className="auth-split">
        {visual}
        <section className="auth-form-side">{form}</section>
      </div>
    </div>
  );
}
