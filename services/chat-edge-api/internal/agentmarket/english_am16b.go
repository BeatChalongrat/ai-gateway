package agentmarket

// The English of the sentence AM16 added for the recipients an admin names (models.go): what a menu
// says when an agent's documents may leave but nobody who runs a model has been named for it.
// One entry per line, sorted by the Thai sentence.

func init() {
	AddEnglish(map[string]string{
		"เอกสารของ agent นี้ส่งออกนอกองค์กรได้ แต่ผู้ดูแลยังไม่ได้ระบุผู้ให้บริการที่รับเอกสารได้สำหรับโมเดลนี้ — ผู้ดูแลเลือกผู้ให้บริการได้ที่แอปผู้ดูแลระบบ": "This agent's documents may leave the organisation, but an admin has not yet named a provider that may receive them for this model — an admin chooses the providers in the admin app",
	})
}
