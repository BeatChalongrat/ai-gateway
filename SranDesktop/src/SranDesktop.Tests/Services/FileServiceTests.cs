using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using SranDesktop.Application.Interfaces;
using SranDesktop.Application.Services;
using Xunit;

namespace SranDesktop.Tests.Services;

public class FileServiceTests
{
    [Fact]
    public async Task Upload_Missing_File_Throws()
    {
        var service = new FileService(Mock.Of<IChatEdgeApiClient>(), NullLogger<FileService>.Instance);

        var act = () => service.UploadAsync("c1", Path.Combine(Path.GetTempPath(), "nope-does-not-exist.txt"));

        await act.Should().ThrowAsync<FileNotFoundException>();
    }

    [Fact]
    public async Task Upload_Passes_Guessed_ContentType_And_Name()
    {
        var tempFile = Path.Combine(Path.GetTempPath(), $"sran-{Guid.NewGuid():N}.pdf");
        await File.WriteAllBytesAsync(tempFile, new byte[] { 1, 2, 3 });
        try
        {
            var api = new Mock<IChatEdgeApiClient>();
            api.Setup(a => a.UploadFileAsync("c1", It.IsAny<Stream>(), It.IsAny<string>(),
                    "application/pdf", 3, It.IsAny<IProgress<double>?>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(new Application.Models.FileAttachmentDto(
                    "f1", "c1", Path.GetFileName(tempFile), "application/pdf", 3, null, "pending",
                    DateTimeOffset.UtcNow))
                .Verifiable();

            var service = new FileService(api.Object, NullLogger<FileService>.Instance);
            var result = await service.UploadAsync("c1", tempFile);

            result.Id.Should().Be("f1");
            api.Verify();
        }
        finally
        {
            File.Delete(tempFile);
        }
    }
}
