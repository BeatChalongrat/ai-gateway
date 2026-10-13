using System.Text;
using FluentAssertions;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Agent;

/// <summary>
/// Rule matrix for the tokenized destructive-command detector that replaced
/// run_command's substring blocklist. Two invariants: every destructive
/// command SHAPE is caught (including wrappers and encoded payloads), and
/// the words involved (format/del/disk/…) never match as flags, switch
/// values, paths, or quoted data.
/// </summary>
public class DestructiveCommandDetectorTests
{
    // ── MUST BLOCK ──────────────────────────────────────────────────

    [Theory]
    // disk format / wipe
    [InlineData("format c:")]
    [InlineData("FORMAT C:")]
    [InlineData("format /q d:")]
    [InlineData("format /fs:ntfs d:")]
    [InlineData("format \"c:\"")]
    [InlineData(@"C:\Windows\System32\format.com d:")]
    [InlineData("echo y | format d: /q")]
    [InlineData("Format-Volume -DriveLetter D")]
    [InlineData("Clear-Disk -Number 1 -RemoveData")]
    [InlineData("Remove-Partition -DiskNumber 0 -PartitionNumber 2")]
    [InlineData("Initialize-Disk -Number 1")]
    [InlineData("diskpart")]
    [InlineData("diskpart /s wipe.txt")]
    [InlineData("mkfs.ext4 /dev/sda1")]
    [InlineData(@"dd if=/dev/zero of=\\.\PhysicalDrive0")]
    [InlineData("dd if=image.iso of=/dev/sda")]
    [InlineData("cipher /w:C:\\")]
    // mass delete on root-ish targets
    [InlineData("del /s /q C:\\")]
    [InlineData("del /s C:\\Users")]
    [InlineData("erase /s /q d:")]
    [InlineData("rd /s /q C:\\Windows")]
    [InlineData("rmdir /s c:")]
    [InlineData("rm -rf /")]
    [InlineData("rm -rf /*")]
    [InlineData("rm -r -f ~")]
    [InlineData("Remove-Item C:\\ -Recurse -Force")]
    [InlineData("Remove-Item -Recurse -Force C:\\Users")]
    // recovery / boot / registry / power
    [InlineData("vssadmin delete shadows /all")]
    [InlineData("wbadmin delete catalog")]
    [InlineData("bcdedit /delete {current}")]
    [InlineData("reg delete hklm\\SYSTEM /f")]
    [InlineData("shutdown /s /t 0")]
    [InlineData("Restart-Computer -Force")]
    // wrappers
    [InlineData("cmd /c format d:")]
    [InlineData("cmd.exe /k \"format d:\"")]
    [InlineData("powershell -Command \"Format-Volume -DriveLetter D\"")]
    [InlineData("powershell -NoProfile Clear-Disk -Number 1")]
    [InlineData("git status && format d:")]
    // fork bomb
    [InlineData(":(){ :|:& };:")]
    public void Blocks_Destructive_Command_Shapes(string command)
    {
        DestructiveCommandDetector.Analyze(command).Should().NotBeNull(
            $"'{command}' is a destructive command shape");
    }

    [Fact]
    public void Blocks_Encoded_PowerShell_That_Decodes_To_A_Destructive_Command()
    {
        var payload = Convert.ToBase64String(Encoding.Unicode.GetBytes("format c:"));
        DestructiveCommandDetector.Analyze($"powershell -EncodedCommand {payload}")
            .Should().NotBeNull();
        DestructiveCommandDetector.Analyze($"powershell -enc {payload}")
            .Should().NotBeNull();
    }

    [Fact]
    public void Blocks_Encoded_PowerShell_That_Cannot_Be_Decoded()
    {
        // What cannot be inspected cannot be cleared — fail-safe.
        var hit = DestructiveCommandDetector.Analyze(
            "powershell -EncodedCommand %%%not-base64%%%");
        hit.Should().NotBeNull();
        hit!.Rule.Should().Be("encoded_command");
    }

    [Fact]
    public void Allows_Encoded_PowerShell_That_Decodes_To_A_Safe_Command()
    {
        var payload = Convert.ToBase64String(Encoding.Unicode.GetBytes("Get-Date"));
        DestructiveCommandDetector.Analyze($"powershell -EncodedCommand {payload}")
            .Should().BeNull();
    }

    // ── MUST PASS ───────────────────────────────────────────────────

    [Theory]
    // the original false positive family: format as a FLAG
    [InlineData("docker images --format \"{{.Repository}}:{{.Tag}}\"")]
    [InlineData("git log --format=%H -5")]
    [InlineData("git log --format short")]
    [InlineData("wmic os get caption /format:csv")]
    [InlineData("dotnet test --logger \"console;format=detailed\"")]
    [InlineData("Format-Table -AutoSize")]
    [InlineData("Get-Process | Format-List Name")]
    // deletion of ordinary targets
    [InlineData("del myfile.txt")]
    [InlineData("del /q build\\output.log")]
    [InlineData("rm -rf node_modules")]
    [InlineData("rm -rf ./build/out")]
    [InlineData("Remove-Item C:\\repo\\build\\obj -Recurse -Force")]
    [InlineData("rd /s /q C:\\repo\\build\\obj")]
    // the words as data / paths / args
    [InlineData("echo format c: is dangerous")]
    [InlineData("git commit -m \"del /s cleanup; format tweaks\"")]
    [InlineData("type C:\\notes\\diskpart_usage.md")]
    [InlineData("cat README.md")]
    [InlineData("wsl --shutdown")]
    [InlineData("Get-Content shutdown.log")]
    // safe tool usage that shares first letters with rules
    [InlineData("bcdedit /enum")]
    [InlineData("reg query hklm\\SOFTWARE")]
    [InlineData("dd if=in.bin of=out.bin bs=4096")]
    [InlineData("powershell -Command \"Get-ChildItem\"")]
    public void Allows_Safe_Commands(string command)
    {
        DestructiveCommandDetector.Analyze(command).Should().BeNull(
            $"'{command}' is safe — the words involved are flags, paths or data");
    }
}
