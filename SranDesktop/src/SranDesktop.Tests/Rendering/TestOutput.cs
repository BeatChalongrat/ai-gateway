namespace SranDesktop.Tests.Rendering;

/// <summary>
/// Scratch sink for measurements that are the POINT of a test rather than its
/// assertion — cache memory at capacity, decoded image size. xUnit swallows
/// Console output written from a worker thread, and these tests all run their
/// bodies on their own STA thread, so the numbers go to a temp file the report
/// can quote.
/// </summary>
internal static class TestOutput
{
    public static void Record(string line) =>
        File.AppendAllText(
            Path.Combine(Path.GetTempPath(), "sran-scroll-perf.txt"),
            line + Environment.NewLine);
}

/// <summary>MotionSettings.OverrideEnabled is process-wide static state, so
/// every class that toggles it must run serially or they flip it under each
/// other — which is exactly how ReducedMotion_LeavesNativeWheelBehaviourExactly
/// AsItWas passed alone and failed in the full suite.</summary>
[Xunit.CollectionDefinition("MotionSettings", DisableParallelization = true)]
public class MotionSettingsCollection { }
