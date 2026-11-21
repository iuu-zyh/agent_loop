using System;
using System.Text;
using ICSharpCode.Decompiler;
using ICSharpCode.Decompiler.CSharp;
using ICSharpCode.Decompiler.TypeSystem;

var dll = args.Length > 0 ? args[0] : @"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mods\GGBH_MOD.dll";
var filter = args.Length > 2 ? args[2] : "";
var settings = new DecompilerSettings() { ThrowOnAssemblyResolveErrors = false };
var decompiler = new CSharpDecompiler(dll, settings);
var sb = new StringBuilder();
foreach (var t in decompiler.TypeSystem.MainModule.TypeDefinitions)
{
    if (filter != "" && t.FullName.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
    sb.AppendLine("//==================== TYPE " + t.FullName + " ====================");
    try { sb.AppendLine(decompiler.DecompileTypeAsString(new FullTypeName(t.FullName))); }
    catch (Exception ex) { sb.AppendLine("// decompile failed: " + ex.Message); }
    sb.AppendLine();
}
var outPath = args.Length > 1 ? args[1] : @"F:\agent_loop\.tmp_decomp\ggbh_mod_full.cs";
System.IO.File.WriteAllText(outPath, sb.ToString(), new UTF8Encoding(false));
Console.WriteLine("ok -> " + outPath);
