// Dictionary composition root.
//
// The dictionary is SPLIT BY AREA (one module per page group) rather than
// living in one 2 000-line file. Two reasons: a page's strings sit next to
// nothing else, so a translator can review one screen at a time; and the
// modules can be edited independently without every change landing in the
// same file.
//
// PARITY IS A COMPILE ERROR, NOT A TEST WARNING. Each module exports `en`
// (canonical) and `th` typed `Record<keyof typeof en, string>`. Add an
// English key without its Thai twin and `next build` / `tsc` fails in that
// module. The runtime test in lib/i18n/dict.test.ts is the second belt: it
// catches an `as any` escape hatch, a key claimed by two modules, an
// English string left sitting on the Thai side, and placeholder drift.
//
// This mirrors apps/web-chat/src/lib/i18n/dict.ts, which applies the same
// `Record<DictKey, string>` device to a single file.
//
// Each module owns a key prefix (see its header). Two modules claiming the
// same key would have the later spread silently win, so the parity test
// fails on any overlap.

import * as accounts from "./accounts";
import * as agentmarket from "./agentmarket";
import * as chatadmin from "./chatadmin";
import * as common from "./common";
import * as dashboard from "./dashboard";
import * as fallback from "./fallback";
import * as infra from "./infra";
import * as nav from "./nav";
import * as observability from "./observability";
import * as portaladmin from "./portaladmin";
import * as tenancy from "./tenancy";
import * as ui from "./ui";

export type Locale = "en" | "th";

export const en = {
  ...common.en,
  ...nav.en,
  ...ui.en,
  ...dashboard.en,
  ...infra.en,
  ...tenancy.en,
  ...portaladmin.en,
  ...observability.en,
  ...chatadmin.en,
  ...agentmarket.en,
  ...accounts.en,
  ...fallback.en,
};

export const th: Record<keyof typeof en, string> = {
  ...common.th,
  ...nav.th,
  ...ui.th,
  ...dashboard.th,
  ...infra.th,
  ...tenancy.th,
  ...portaladmin.th,
  ...observability.th,
  ...chatadmin.th,
  ...accounts.th,
  ...agentmarket.th,
  ...fallback.th,
};

export type DictKey = keyof typeof en;

export const dictionaries: Record<Locale, Record<DictKey, string>> = {
  en,
  th,
};

/// The area modules, exposed so the parity test can report WHICH module a
/// mismatch came from instead of just "the dictionary". Every module that is
/// spread into `en` above is listed here: one that is composed but not
/// listed is read by nobody's per-module check (dict.am10.test.ts compares
/// the two sets).
export const modules = {
  common,
  nav,
  ui,
  dashboard,
  infra,
  tenancy,
  portaladmin,
  observability,
  fallback,
  chatadmin,
  agentmarket,
  accounts,
} as const;
