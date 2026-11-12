import assert from "node:assert/strict";
import test from "node:test";
import { addAuthNotice, markPendingUnknown, stepLabel, stepState } from "../src/lib.ts";

const call = { id: "call-1", name: "github__get_commit", args: {} };
const result = (status, content) => ({ role: "tool", tool_call_id: call.id, name: call.name, status, content });

test("distinguishes blocked calls from other errors", () => {
  assert.equal(stepState({ call, result: result("error", "Not allowed: above connection level") }), "refused");
  assert.equal(stepState({ call, result: result("error", "Connection timed out") }), "error");
  assert.equal(stepLabel({ call, result: result("success", "{}") }), "Returned");
});

test("closed responses mark unfinished calls as unknown, never cancelled", () => {
  const messages = [{ role: "assistant", id: "a", content: "", tool_calls: [call] }];
  for (const stopped of [true, false]) {
    const updated = markPendingUnknown(messages, stopped);
    assert.equal(stepLabel({ call, result: updated[1] }), "Outcome unknown");
    assert.equal(markPendingUnknown(updated, stopped).length, 2);
  }
});

test("closed responses do not replace returned tool results", () => {
  const messages = [
    { role: "assistant", id: "a", content: "", tool_calls: [call] },
    result("success", "{}"),
  ];
  assert.equal(markPendingUnknown(messages, false), messages);
});

test("combines sign-in notices only for the same profile and login method", () => {
  const first = { type: "auth_required", source: "AWS connection personal", profile: null, method: "login", error_type: "LoginRefreshRequired" };
  const second = { ...first, source: "Bedrock" };
  const merged = addAuthNotice(addAuthNotice([], first), second);
  assert.equal(merged.length, 1);
  assert.deepEqual(merged[0].sources, ["AWS connection personal", "Bedrock"]);
  assert.equal(addAuthNotice(merged, { ...first, profile: "work" }).length, 2);
  assert.equal(addAuthNotice(merged, { ...first, method: "sso" }).length, 2);
});

test("changes waiting for approval are neither running nor unknown", () => {
  const messages = [{ role: "assistant", id: "a", content: "", tool_calls: [call] }];
  assert.equal(markPendingUnknown(messages, false, new Set([call.id])).length, 1);
  assert.equal(stepLabel({ call, awaiting: true }), "Awaiting approval");
  assert.equal(stepState({ call, result: result("error", "User rejected the tool call") }), "rejected");
  assert.equal(stepState({ call, result: result("error", "Not run: the user sent a new message") }), "rejected");
});
