using System.Text.RegularExpressions;
using SranDesktop.Application.Models;
using SranDesktop.Domain.Entities;

namespace SranDesktop.Application.Services;

public sealed partial class GoalInterpreter
{
    private static readonly string[] ActionTerms =
    [
        "open ", "close ", "launch ", "create ", "write ", "edit ", "move ", "rename ", "run ",
        "build ", "test ", "debug ", "fix ", "install ", "configure ", "set up ", "click ", "type ",
        "inspect ", "organize ", "research ", "fill ", "calculate ", "compute ",
        "archive ", "extract ", "commit ", "push ",
        // Additional common command verbs (trailing space keeps them from matching
        // inside longer words, e.g. "run" inside "truncate").
        "manage ", "arrange ", "tidy ", "sort ", "clean ", "update ", "delete ", "remove ",
        "download ", "upload ", "generate ", "summarize ", "analyze ", "convert ", "send ",
        "copy ", "list ", "search ", "start ", "stop ", "prepare ", "make ",
        "เปิด", "สร้าง", "เขียน", "แก้", "ย้าย", "เปลี่ยนชื่อ", "รัน", "ทำให้",
        "ติดตั้ง", "ตั้งค่า", "กด", "ปิด", "พิมพ์", "กรอก", "คำนวณ", "จัดระเบียบ", "ตรวจแล้ว",
        // More Thai command verbs. Chosen to avoid colliding with common non-command
        // words (e.g. bare "ลบ" avoided because it is a substring of "กลับ").
        "จัดการ", "จัดเรียง", "ค้นหา", "ตรวจสอบ", "อัปเดต", "อัพเดต", "ดาวน์โหลด",
        "อัปโหลด", "สรุป", "แปลง", "จัดเก็บ", "รวบรวม"
    ];

    private static readonly string[] LongHorizonTerms =
    [
        "install", "toolchain", "dependency", "project", "production", "configure",
        "ติดตั้ง", "โปรเจกต์", "dependency", "production", "ตั้งค่า", "พร้อมใช้งาน"
    ];

    public GoalInterpretation Interpret(string request, bool hasPriorContext = false)
    {
        var trimmed = request.Trim();
        if (trimmed.Length == 0)
            return new(false, AgentTaskMode.SimpleAction, null, null);

        var lower = trimmed.ToLowerInvariant();
        // Substring terms catch fixed phrases; the verb regex additionally catches
        // English inflections ("organized", "cleaning up", "installs") that the
        // trailing-space terms miss, without matching those verbs inside longer words.
        var actionable = ActionTerms.Any(lower.Contains) || ActionableVerbRegex().IsMatch(lower);
        if (!actionable)
            return new(false, AgentTaskMode.SimpleAction, null, null);

        var now = DateTimeOffset.UtcNow;
        var risk = InferRisk(lower);
        var mode = InferMode(lower);
        var constraints = ExtractConstraints(trimmed);
        var nonGoals = ExtractNonGoals(trimmed);
        var missing = new List<string>();
        string? clarification = null;
        if (!hasPriorContext && ReferencesUnspecifiedObject(lower))
        {
            missing.Add("The referenced target has not been identified.");
            clarification = "Which specific application, file, or target should this task use?";
        }

        var goalId = "goal_" + Guid.NewGuid().ToString("N");
        var goal = new GoalSpec
        {
            GoalId = goalId,
            OriginalUserRequest = trimmed,
            NormalizedGoal = NormalizeGoal(trimmed),
            DesiredOutcome = DescribeOutcome(trimmed),
            Constraints = constraints,
            NonGoals = nonGoals,
            MissingInformation = missing,
            RiskLevel = risk,
            DefinitionOfDone = BuildDefinitionOfDone(goalId, lower, constraints, mode),
            CreatedAtUtc = now,
            UpdatedAtUtc = now
        };
        return new(true, mode, goal, clarification);
    }

    private static AgentTaskMode InferMode(string lower)
    {
        if (LongHorizonTerms.Any(lower.Contains))
            return AgentTaskMode.LongHorizonTask;
        var separators = lower.Count(c => c is ',' or ';' or '\n');
        return separators > 0 || lower.Contains(" and ") || lower.Contains("แล้ว")
            || lower.Contains("จากนั้น")
            ? AgentTaskMode.StructuredTask
            : AgentTaskMode.SimpleAction;
    }

    private static AgentRiskLevel InferRisk(string lower)
    {
        if (lower.Contains("password") || lower.Contains("credential")
            || lower.Contains("security") || lower.Contains("production")
            || lower.Contains("รหัสผ่าน") || lower.Contains("ความปลอดภัย"))
            return AgentRiskLevel.Critical;
        if (lower.Contains("install") || lower.Contains("uninstall")
            || lower.Contains("admin") || lower.Contains("delete")
            || lower.Contains("ติดตั้ง") || lower.Contains("ลบ"))
            return AgentRiskLevel.High;
        if (lower.Contains("write") || lower.Contains("edit") || lower.Contains("move")
            || lower.Contains("แก้") || lower.Contains("ย้าย") || lower.Contains("เขียน"))
            return AgentRiskLevel.Medium;
        return AgentRiskLevel.Low;
    }

    private static List<string> ExtractConstraints(string request)
    {
        var result = new List<string>();
        foreach (Match match in ConstraintRegex().Matches(request))
        {
            var value = match.Value.Trim().TrimEnd('.', ',', ';');
            if (value.Length > 0 && !result.Contains(value, StringComparer.OrdinalIgnoreCase))
                result.Add(value);
        }
        return result;
    }

    private static List<string> ExtractNonGoals(string request)
    {
        var result = new List<string>();
        foreach (Match match in NonGoalRegex().Matches(request))
        {
            var value = match.Value.Trim().TrimEnd('.', ',', ';');
            if (value.Length > 0) result.Add(value);
        }
        return result;
    }

    private static List<DefinitionOfDoneItem> BuildDefinitionOfDone(
        string goalId,
        string lower,
        IReadOnlyList<string> constraints,
        AgentTaskMode mode)
    {
        var items = new List<DefinitionOfDoneItem>();
        void Add(string suffix, string description, string verification, string evidence) =>
            items.Add(new DefinitionOfDoneItem
            {
                Id = $"{goalId}_dod_{suffix}",
                Description = description,
                VerificationType = verification,
                RequiredEvidence = evidence
            });

        if (mode == AgentTaskMode.SimpleAction)
        {
            Add("outcome", "The requested target state is observed.",
                "IndependentStateObservation", "Verified tool or UI state evidence");
        }
        else
        {
            Add("environment", "The relevant environment and prerequisites are inspected.",
                "WorldState", "Versioned world-state evidence");
            Add("outcome", "The requested outcome works in the target environment.",
                "IndependentVerification", "Successful independent verification evidence");
        }

        if (lower.Contains("install") || lower.Contains("ติดตั้ง"))
        {
            Add("approval", "Consequential installation actions were explicitly approved.",
                "ApprovalRecord", "Scoped, unexpired approval evidence");
            Add("version", "The resulting executable or tool version is verified.",
                "ProcessOutput", "Executable identity and version output");
        }

        if (lower.Contains("project") || lower.Contains("โปรเจกต์")
            || lower.Contains("build") || lower.Contains("run"))
            Add("project", "The project build or run check succeeds.",
                "ProcessExit", "Command exit code and output evidence");

        if (constraints.Count > 0)
            Add("constraints", "All user constraints remain satisfied.",
                "ConstraintAudit", "Before/after evidence for each persisted constraint");

        return items;
    }

    private static bool ReferencesUnspecifiedObject(string lower) =>
        lower.Contains("this program") || lower.Contains("this file")
        || lower.Contains("โปรแกรมนี้") || lower.Contains("ไฟล์นี้")
        || lower == "ปิดอันนี้" || lower == "close this";

    private static string NormalizeGoal(string request) =>
        Regex.Replace(request.Trim(), @"\s+", " ");

    private static string DescribeOutcome(string request) =>
        $"Verified completion of: {NormalizeGoal(request)}";

    // A prohibition clause runs to the end of the sentence: stop only at a period
    // or a line break, NOT at commas/semicolons — so an enumerated constraint like
    // "do not delete, rename, or move any files" is captured whole instead of being
    // truncated at the first comma. (Trailing punctuation is trimmed by the caller.)
    [GeneratedRegex(
        @"(?i)(?:ห้าม[^.\r\n]+|must\s+not[^.\r\n]+|do\s+not[^.\r\n]+|don't[^.\r\n]+|without\s+[^.\r\n]+)")]
    private static partial Regex ConstraintRegex();

    [GeneratedRegex(@"(?i)(?:ไม่ต้อง[^.\r\n]+|not\s+required[^.\r\n]+|out\s+of\s+scope[^.\r\n]+)")]
    private static partial Regex NonGoalRegex();

    // English command verbs with optional inflections (s/es/ed/ing). The base form
    // ends the alternation so "install"→"installed"/"installing" match while
    // "installation" (a noun) does not: after the optional suffix a word boundary
    // is required, which the trailing "ation" defeats. Verbs whose "- e" is dropped
    // before "-ing"/"-ed" (organize→organizing) are handled by the suffix list.
    [GeneratedRegex(
        @"(?i)\b(?:open|clos|launch|creat|delet|remov|renam|mov|writ|edit|build|test|debug|fix|install|configur|organiz|arrang|tidy|clean|sort|updat|download|upload|generat|summariz|analyz|convert|extract|archiv|inspect|research|calculat|comput|manag|prepar)(?:e|es|ed|ing|s)?\b")]
    private static partial Regex ActionableVerbRegex();
}
