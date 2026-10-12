package agentmarket

import "context"

// AM15 — a run that cannot reach a model it is allowed to use stops and waits.
//
// Found on the test stack with the in-house model taken away (12 Oct 2026): every one of the five
// accounting agents — whose documents may not leave the organisation, so no outside model may stand in
// — went on regardless, delivered its files and was charged for them: the words typed with the files
// unread ("the documents are the usual ones"), a scan read by OCR alone with the step still naming the
// model, the report's commentary written by rules. None stopped, and none said the model was away.
//
// Now such a run waits (awaiting_input, reason "model_unreachable"): its thread says so in the run's
// language, nothing is rendered or delivered, and the step that could not ask stays unfinished. The
// runner looks for the model every few minutes while a run waits for it (runner/modeldown.go) and puts
// the run back in the queue when it answers: steps already done are read back, the unfinished one asks
// again. A person can cancel a waiting run like any other; cancelled, it is charged nothing.

// ReasonModelUnreachable is the status reason of a run that waits for its model — and of one that
// stopped for good because the model was found away again each time it went on.
const ReasonModelUnreachable = "model_unreachable"

// MaxModelWaits is how many times one run may stop to wait for its model. A run goes on only after
// the model answered a word (the runner's watch), so a run that finds it away again each time is
// asking something the model does not answer in time while it answers everything else. It does not
// wait and go on for ever: after this many waits it ends failed, saying so, charged nothing, and can
// be tried again with its files.
const MaxModelWaits = 3

func init() {
	failureCopies[ReasonModelUnreachable] = failureCopy{"ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้หลายครั้งติดกัน รอบนี้จึงหยุดไว้โดยยังไม่ได้ทำไฟล์", nextLater, true}
}

// ModelWaits is how many times a run has stopped to wait for its model (each wait left its notice).
func (s *Store) ModelWaits(ctx context.Context, run Run) (int, error) {
	var n int
	err := s.Pool.QueryRow(ctx, `SELECT count(*) FROM agent_market.agent_thread_items
		WHERE run_id = $1 AND kind = 'notice' AND payload->>'reason' = $2 AND payload->>'tone' = 'warning'`,
		run.ID, ReasonModelUnreachable).Scan(&n)
	return n, err
}

// ModelAwayNotice is what the thread says when a run stops to wait for its model. (Not a word about
// what it costs: what a run is charged is the admin's to read — AM11b.)
func ModelAwayNotice() PersonText {
	return Text("หยุดรอไว้ก่อนครับ: ตอนนี้ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้ จึงยังไม่ทำไฟล์ออกมา — เมื่อติดต่อโมเดลได้ รอบนี้จะทำต่อจากขั้นที่ค้างไว้เอง ไม่ต้องส่งไฟล์หรือพิมพ์ใหม่ ไฟล์และคำตอบที่ให้ไว้ยังอยู่ครบ")
}

// ModelBackNotice is what the thread says when a run that waited for its model goes on.
func ModelBackNotice() PersonText {
	return Text("ติดต่อโมเดลได้แล้ว รอบนี้ทำต่อจากขั้นที่ค้างไว้ครับ")
}

// ModelAwayAnswer is what a typed question gets when only a model could answer it and none can be
// reached — it used to read as the agent not being sure of the figures.
func ModelAwayAnswer() PersonText {
	return Text("ตอนนี้ติดต่อโมเดลที่ agent นี้ใช้ไม่ได้ จึงยังตอบคำถามนี้ไม่ได้ครับ ลองถามอีกครั้งภายหลัง — ตัวเลขและที่มาดูได้ในไฟล์ของรอบนี้ และในหน้า “ขั้นตอนและที่มาของตัวเลข”")
}

// ModelParked lists the runs that wait for their model, oldest first.
func (s *Store) ModelParked(ctx context.Context) ([]Run, error) {
	rows, err := s.Pool.Query(ctx, `SELECT `+runCols+` FROM agent_market.agent_runs
		WHERE status = 'awaiting_input' AND status_reason = $1 AND NOT cancel_requested ORDER BY created_at`, ReasonModelUnreachable)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Run
	for rows.Next() {
		x, err := scanRun(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// ResumeModelParked puts back in the queue every run that waits for its model, and says so in its
// thread. The caller has just seen the model answer.
func (s *Store) ResumeModelParked(ctx context.Context) (int, error) {
	runs, err := s.ModelParked(ctx)
	if err != nil {
		return 0, err
	}
	n := 0
	for _, run := range runs {
		if ok, err := s.Resume(ctx, s.Pool, run.ID); err == nil && ok {
			_ = s.PostNotice(ctx, run, ModelBackNotice(), "")
			n++
		}
	}
	return n, nil
}
