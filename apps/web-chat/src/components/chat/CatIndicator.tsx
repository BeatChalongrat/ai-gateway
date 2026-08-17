"use client";

// Transient status indicator: a breathing cat mark + shimmer status label.
// Replaces the generic three typing dots while the assistant is between
// phases (waiting for the first token, searching, …). It is NOT an avatar —
// it disappears as soon as content streams in. The cat inherits
// `currentColor` so light/dark theming is free, and the breathe animation
// lives in globals.css (`catBreathe`) where prefers-reduced-motion turns
// it off. The same M/L/Z path data drives the WPF version (Path.Data).

import { useT } from "@/lib/i18n";
import type { DictKey } from "@/lib/i18n/dict";

const CAT_BODY =
  "M 8 201 L 4 212 L 2 224 L 1 225 L 1 255 L 6 273 L 12 285 L 20 296 L 29 305 L 42 315 L 52 320 L 54 322 L 58 323 L 62 326 L 64 326 L 77 332 L 88 335 L 91 337 L 97 338 L 102 342 L 101 344 L 90 354 L 52 380 L 43 390 L 43 393 L 46 395 L 76 395 L 77 394 L 106 393 L 107 392 L 116 392 L 117 391 L 134 391 L 135 390 L 151 389 L 152 388 L 176 387 L 177 386 L 184 386 L 185 385 L 216 383 L 217 382 L 225 382 L 226 381 L 234 381 L 235 380 L 245 380 L 246 379 L 254 379 L 255 378 L 262 378 L 263 377 L 274 377 L 275 376 L 283 376 L 290 374 L 311 373 L 312 372 L 318 372 L 319 371 L 325 371 L 326 370 L 351 368 L 314 370 L 313 371 L 304 371 L 303 372 L 288 372 L 287 373 L 276 373 L 275 374 L 252 375 L 251 376 L 223 377 L 222 378 L 199 379 L 198 380 L 177 380 L 176 381 L 164 381 L 163 382 L 150 382 L 149 383 L 120 383 L 119 384 L 87 384 L 86 383 L 81 383 L 80 380 L 83 376 L 98 362 L 101 358 L 128 334 L 89 320 L 71 311 L 60 304 L 47 294 L 35 281 L 27 267 L 24 258 L 24 255 L 23 254 L 23 236 L 24 235 L 24 230 L 26 226 L 28 216 L 35 202 L 38 198 L 54 183 L 74 172 L 82 170 L 85 168 L 88 168 L 101 164 L 121 162 L 122 161 L 131 161 L 132 160 L 176 160 L 177 161 L 190 161 L 191 162 L 200 162 L 201 163 L 225 165 L 232 167 L 239 167 L 240 168 L 249 169 L 254 171 L 261 171 L 270 174 L 274 174 L 275 175 L 279 175 L 280 176 L 295 179 L 294 178 L 287 177 L 284 175 L 281 175 L 269 171 L 266 171 L 253 167 L 249 167 L 236 163 L 231 163 L 222 160 L 215 160 L 214 159 L 211 159 L 206 157 L 201 157 L 200 156 L 193 156 L 192 155 L 171 153 L 170 152 L 145 151 L 144 150 L 108 150 L 107 151 L 96 151 L 95 152 L 88 152 L 87 153 L 72 155 L 71 156 L 64 157 L 58 160 L 55 160 L 44 165 L 26 177 L 19 184 Z";

const CAT_HEAD =
  "M 336 5 L 335 5 L 326 23 L 310 49 L 303 58 L 293 67 L 275 65 L 274 64 L 253 63 L 252 64 L 239 64 L 238 65 L 227 66 L 219 69 L 215 65 L 213 60 L 211 58 L 194 24 L 192 22 L 192 20 L 190 18 L 182 1 L 181 1 L 182 16 L 183 17 L 183 24 L 184 25 L 184 40 L 185 41 L 185 51 L 186 52 L 186 61 L 187 62 L 188 84 L 178 93 L 169 104 L 159 122 L 156 132 L 168 115 L 180 103 L 194 92 L 204 87 L 206 85 L 208 85 L 220 79 L 226 77 L 230 77 L 234 75 L 238 75 L 239 74 L 246 74 L 247 73 L 276 73 L 277 74 L 283 74 L 290 77 L 293 77 L 301 81 L 308 86 L 315 93 L 320 100 L 324 108 L 328 122 L 328 129 L 329 130 L 329 153 L 328 154 L 327 169 L 324 178 L 324 182 L 322 186 L 323 193 L 324 194 L 324 202 L 325 203 L 325 224 L 324 225 L 323 240 L 321 245 L 321 251 L 318 258 L 317 264 L 310 279 L 310 281 L 296 302 L 279 319 L 272 324 L 253 334 L 242 338 L 239 338 L 235 340 L 231 340 L 226 342 L 222 342 L 217 344 L 204 344 L 203 345 L 168 345 L 167 344 L 161 344 L 162 345 L 181 346 L 182 347 L 223 347 L 224 346 L 233 346 L 234 345 L 251 343 L 255 341 L 259 341 L 269 337 L 272 337 L 291 328 L 308 316 L 319 304 L 329 288 L 329 286 L 332 281 L 333 276 L 336 270 L 336 266 L 338 260 L 338 254 L 339 253 L 339 234 L 338 233 L 338 227 L 336 221 L 336 216 L 332 206 L 332 203 L 330 200 L 328 193 L 326 191 L 326 187 L 332 173 L 332 169 L 336 159 L 336 153 L 337 152 L 338 142 L 339 141 L 339 120 L 338 119 L 338 114 L 336 109 L 336 90 L 335 89 Z";

export function CatIndicator({
  labelKey,
  label,
}: {
  labelKey: DictKey;
  /** Pre-resolved label text — overrides labelKey. Used by
   *  ToolActivityIndicator, whose generic fallback needs a {tool}
   *  variable that a bare dict key can't carry. */
  label?: string;
}) {
  const t = useT();
  // Visual-only on purpose: screen-reader announcements come from the
  // single persistent live region in ChatThread. A role="status" here
  // would (a) double-announce and (b) be unreliable anyway — a live
  // region inserted together with its content is often skipped by
  // NVDA/JAWS.
  return (
    <div
      className="cat-indicator"
      style={{ display: "flex", alignItems: "center", gap: "9px" }}
    >
      <svg
        viewBox="0 0 366 397"
        width={22}
        height={24}
        fill="none"
        aria-hidden
      >
        <path fill="currentColor" d={CAT_BODY} />
        <path fill="currentColor" d={CAT_HEAD} />
      </svg>
      <span className="shimmer-text text-sm font-medium">
        {label ?? t(labelKey)}
      </span>
    </div>
  );
}
