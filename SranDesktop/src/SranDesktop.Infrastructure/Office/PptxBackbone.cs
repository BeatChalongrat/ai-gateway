using System.Text;
using DocumentFormat.OpenXml.Packaging;
using P = DocumentFormat.OpenXml.Presentation;

namespace SranDesktop.Infrastructure.Office;

/// <summary>
/// The minimum theme/master/layout backbone a PresentationML package needs to
/// open. Shared verbatim by the in-app plan executor and the isolated document
/// worker (which source-links this file) so a deck built out of process is
/// byte-for-byte the same package.
/// </summary>
internal static class PptxBackbone
{
    /// <summary>Creates master + layout + theme and returns the layout every
    /// slide attaches to, along with the slide-id list to append into.</summary>
    public static (SlideLayoutPart Layout, P.SlideIdList SlideIds) Build(
        PresentationPart presentationPart,
        long slideWidth,
        long slideHeight)
    {
        var masterPart = presentationPart.AddNewPart<SlideMasterPart>("rIdMaster");
        var layoutPart = masterPart.AddNewPart<SlideLayoutPart>("rIdLayout");
        var themePart = masterPart.AddNewPart<ThemePart>("rIdTheme");
        layoutPart.AddPart(masterPart, "rId1");

        WriteRawXml(themePart, ThemeXml);
        WriteRawXml(layoutPart, LayoutXml);
        WriteRawXml(masterPart, MasterXml);

        var slideIds = new P.SlideIdList();
        var presentation = new P.Presentation();
        presentation.Append(new P.SlideMasterIdList(new P.SlideMasterId
        {
            Id = 2147483648U,
            RelationshipId = "rIdMaster",
        }));
        presentation.Append(slideIds);
        presentation.Append(new P.SlideSize
        {
            Cx = (int)slideWidth,
            Cy = (int)slideHeight,
        });
        presentation.Append(new P.NotesSize { Cx = 6858000, Cy = 9144000 });
        presentationPart.Presentation = presentation;
        return (layoutPart, slideIds);
    }

    public static void WriteRawXml(OpenXmlPart part, string xml)
    {
        using var stream = part.GetStream(FileMode.Create, FileAccess.Write);
        using var writer = new StreamWriter(stream, new UTF8Encoding(false));
        writer.Write(xml);
    }

    private const string ThemeXml =
        """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme">
          <a:themeElements>
            <a:clrScheme name="Office">
              <a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
              <a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
              <a:dk2><a:srgbClr val="0A1230"/></a:dk2>
              <a:lt2><a:srgbClr val="F6F7F9"/></a:lt2>
              <a:accent1><a:srgbClr val="2E75B6"/></a:accent1>
              <a:accent2><a:srgbClr val="10B981"/></a:accent2>
              <a:accent3><a:srgbClr val="F59E0B"/></a:accent3>
              <a:accent4><a:srgbClr val="8B5CF6"/></a:accent4>
              <a:accent5><a:srgbClr val="EC4899"/></a:accent5>
              <a:accent6><a:srgbClr val="06B6D4"/></a:accent6>
              <a:hlink><a:srgbClr val="0563C1"/></a:hlink>
              <a:folHlink><a:srgbClr val="954F72"/></a:folHlink>
            </a:clrScheme>
            <a:fontScheme name="Office">
              <a:majorFont>
                <a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/>
              </a:majorFont>
              <a:minorFont>
                <a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>
              </a:minorFont>
            </a:fontScheme>
            <a:fmtScheme name="Office">
              <a:fillStyleLst>
                <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
                <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
                <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
              </a:fillStyleLst>
              <a:lnStyleLst>
                <a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
                <a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
                <a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>
              </a:lnStyleLst>
              <a:effectStyleLst>
                <a:effectStyle><a:effectLst/></a:effectStyle>
                <a:effectStyle><a:effectLst/></a:effectStyle>
                <a:effectStyle><a:effectLst/></a:effectStyle>
              </a:effectStyleLst>
              <a:bgFillStyleLst>
                <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
                <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
                <a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
              </a:bgFillStyleLst>
            </a:fmtScheme>
          </a:themeElements>
        </a:theme>
        """;

    private const string MasterXml =
        """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
          <p:cSld>
            <p:bg><p:bgPr><a:solidFill><a:schemeClr val="lt1"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
            <p:spTree>
              <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
              <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
            </p:spTree>
          </p:cSld>
          <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
          <p:sldLayoutIdLst>
            <p:sldLayoutId id="2147483649" r:id="rIdLayout"/>
          </p:sldLayoutIdLst>
        </p:sldMaster>
        """;

    private const string LayoutXml =
        """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">
          <p:cSld name="Blank">
            <p:spTree>
              <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
              <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
            </p:spTree>
          </p:cSld>
          <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
        </p:sldLayout>
        """;
}
