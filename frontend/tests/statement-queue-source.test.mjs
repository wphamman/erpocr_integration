import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const meta = readFileSync(new URL("../src/lib/doctypeMeta.tsx", import.meta.url), "utf8");
const card = readFileSync(new URL("../src/components/QueueCard.tsx", import.meta.url), "utf8");
const list = readFileSync(new URL("../src/pages/QueueList.tsx", import.meta.url), "utf8");
const statementConfig = meta.slice(meta.indexOf('\t"OCR Statement": {'));

test("statement is routed as a first-class queue with direct Desk drill-through", () => {
	assert.match(meta, /"OCR Statement": "ocr-statement"/);
	assert.match(meta, /"OCR Statement": \{\s*statuses: STATEMENT_ACTIONABLE_STATUSES,/);
	assert.match(list, /href=\{`\/app\/\$\{DESK_SLUG\[doctype\]\}\/\$\{encodeURIComponent/);
});

test("status buckets are per doctype and Reviewed is terminal", () => {
	assert.match(
		meta,
		/STATEMENT_ACTIONABLE_STATUSES = \[\s*"Pending",\s*"Extracting",\s*"Reconciled",\s*"Error",\s*\] as const/,
	);
	assert.doesNotMatch(
		meta.match(/STATEMENT_ACTIONABLE_STATUSES = \[[\s\S]*?\] as const/)?.[0] ?? "",
		/Reviewed/,
	);
	assert.equal((meta.match(/statuses: DOCUMENT_ACTIONABLE_STATUSES/g) ?? []).length, 3);
	assert.match(card, /config\.statuses\.map/);
	assert.match(list, /config\.statuses\.includes\(status\)/);
	assert.match(list, /config\.statuses\.map/);
});

test("statement list requests the required work fields", () => {
	for (const field of [
		"name",
		"supplier",
		"supplier_name_ocr",
		"statement_date",
		"period_from",
		"period_to",
		"closing_balance",
		"currency",
		"mismatch_count",
		"missing_count",
		"not_in_statement_count",
		"creation",
	]) {
		assert.match(statementConfig, new RegExp(`"${field}"`), `missing statement field ${field}`);
	}
});
