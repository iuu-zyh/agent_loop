using System;
using AgentLoopBridge;
class Program
{
    static int count;
    static void Check(bool value, string name) { if (!value) throw new Exception(name); count++; }
    static void Main()
    {
        for (int flags=0; flags<8; flags++)
            Check(TeamBattleAdmissionCore.CanInstallHurtGuards((flags&1)!=0,(flags&2)!=0,(flags&4)!=0)==(flags==7),"剧情挂载只允许完整世界就绪"+flags);
        var c = new TeamBattleAdmissionCore();
        Check(!c.HasTeam(10), "空队不能许可");
        c.Register(10, new[]{"mate1", "mate2"}, 5);
        Check(c.HasTeam(10), "真实队友可用");
        Check(!c.HasTeam(11), "不借用上场许可");
        Check(c.CanUseSlot(10,5,"mate1"), "原版占3/4时队友追加5");
        Check(!c.CanUseSlot(10,3,"enemy"), "原版敌人槽不当队友");
        Check(!c.CanUseSlot(10,5,"enemy"), "槽身份变更拒绝");
        Check(!c.CanUseSlot(11,5,"mate1"), "跨场槽拒绝");
        Check(c.Slots.Length==2 && c.Slots[0]==5 && c.Slots[1]==6, "仅生成实际两位");
        Check(!c.Participated("mate1"), "仅注入未生成不算参战");
        Check(!c.HasSpawned(10), "只有登记不可置入场标志");
        Check(!c.CanHurt("mate2",false,true,false), "只有登记未生成不可重伤");
        Check(!c.CaptureUnit(10,0,"mate1") && !c.HasSpawned(10), "空生成返回值不能置标志");
        Check(!c.CaptureUnit(10,1,"enemy"), "同名敌人的ID不可绑定");
        Check(c.CaptureUnit(10,2,"mate1"), "真实生成返回值可绑定");
        Check(c.HasSpawned(10) && !c.HasSpawned(11), "仅本场真实生成允许置标志");
        Check(c.CaptureUnit(10,2,"mate1"), "同一控制器重复回调幂等");
        Check(!c.CaptureUnit(10,3,"mate1"), "同一队友不能重复登记控制器");
        Check(!c.CaptureUnit(10,2,"mate2"), "控制器不能改认其他队友");
        Check(c.Participated("mate1") && !c.Participated("mate2"), "战后只结算实际生成者");
        string id;
        Check(c.ResolveUnit(10,2,out id) && id=="mate1", "早期没有worldUnitData仍认控制器");
        Check(!c.ResolveUnit(10,1,out id), "敌人控制器拒绝");
        Check(!c.ResolveUnit(11,2,out id), "跨场控制器拒绝");
        Check(c.CanHurt("mate1",false,true,false), "真实倒地队友保留重伤");
        Check(!c.CanHurt("mate1",false,false,false), "存活队友不重伤");
        Check(!c.CanHurt("mate1",true,true,false), "玩家不受队友伤");
        Check(!c.CanHurt("enemy",false,true,false), "敌人死亡不触发队友伤");
        Check(!c.CanHurt("mate1",false,true,true), "每个伤者只开一次");
        c.Reset(); Check(!c.HasTeam(10) && !c.HasSpawned(10) && !c.ResolveUnit(10,2,out id), "读档清全状态");
        c.Register(10,new[]{"", "mate1", "mate1"});
        Check(!c.CanUseSlot(10,3,"mate1") && c.CanUseSlot(10,4,"mate1") && c.Slots.Length==1,"空槽不挪位且去重");
        c.Register(10,new[]{"mate1","mate2"},10);
        Check(c.Slots.Length==1 && c.Slots[0]==10,"原版GetUnitWorld槽上限");
        c.Register(10,new string[0]); Check(!c.HasTeam(10),"注入零人不置标志");
        c.Register(0,new[]{"mate1"});Check(!c.HasTeam(0),"未入战不许可");
        for(int bits=0;bits<16;bits++) {
            bool m=(bits&1)!=0, off=(bits&2)!=0, duel=(bits&4)!=0, yes=(bits&8)!=0;
            Check(TeamBattleAdmissionCore.CanStart(m,off,duel,yes)==(m && !off && (!duel || yes)),"组队同意真值表"+bits);
        }
        Check(TeamBattleAdmissionCore.IsSelection(new[]{"addBattleNPCData","-448","0"},"-448"),"自己的选人");
        Check(!TeamBattleAdmissionCore.IsSelection(new[]{"addBattleNPCData","other","0"},"-448"),"其他mod选人放行");
        Check(!TeamBattleAdmissionCore.IsSelection(null,"-448"),"空命令");
        string selection="addBattleNPCData_-448_0";
        string spawn="addBattleUnit_3_-1_10_2_1_2_0";
        string flag="setBattleData_alTeamIn_ok";
        string mark="_@Team:-448";
        string commands=selection+"|"+spawn+"|"+flag;
        Check(TeamBattleCommandCore.Rewrite(commands,"-448","-450")==selection+"|"+spawn+mark+"|"+flag+mark,"标记归属但不跳过原队列");
        string extra=selection+"|wait_1|"+spawn+"|custom_native_command|"+flag;
        Check(TeamBattleCommandCore.Rewrite(extra,"-448","-450")==selection+"|wait_1|"+spawn+mark+"|custom_native_command|"+flag+mark,"保留附加命令及延时顺序");
        Check(TeamBattleCommandCore.Rewrite("other|"+spawn+"|"+flag,"-448","-450")=="other|"+spawn+"|"+flag,"没有自己的选人不认领原版生成");
        string foreign=selection+"|addBattleNPCData_123_0|"+spawn;
        Check(TeamBattleCommandCore.Rewrite(foreign,"-448","-450")==foreign,"外部条件组结束己方作用域");
        string rewritten=TeamBattleCommandCore.Rewrite(extra,"-448","-450");
        Check(TeamBattleCommandCore.Rewrite(rewritten,"-448","-450")==rewritten,"重复编译命令幂等");
        Check(TeamBattleCommandCore.Rewrite(" "+selection+" | "+spawn+" ","-448","-450")==selection+"|"+spawn+mark,"自己命令允许边界空白");
        Check(TeamBattleCommandCore.Rewrite(selection+"|addBattleUnit_6_-1_10_2_1_2_0","-448","-450")==selection+"|addBattleUnit_6_-1_10_2_1_2_0","不占用模板以外的原生槽");
        Check(TeamBattleCommandCore.Rewrite("unitAddEffect_4_-450|unitAddEffect_4_123","-448","-450")=="unitAddEffect_4_-450"+mark+"|unitAddEffect_4_123","只标记自己的重伤效果");
        Check(TeamBattleCommandCore.Rewrite(null,"-448","-450")==null,"空命令原样返回");
        Check(TeamBattleCommandCore.MemberIndex("3")==0 && TeamBattleCommandCore.MemberIndex("5")==2 && TeamBattleCommandCore.MemberIndex("6")==-1,"队友模板位置边界");
        Check(!TeamBattleCommandCore.IsMarked(new[]{"x","@Team:other"},"-448",1),"标识不可跨模组");
        var remapped=TeamBattleCommandCore.Remap(new[]{"unitAddEffect","3","-450","@Team:-448"},7);
        Check(string.Join("_",remapped)=="unitAddEffect_7_-450","执行时重映射位置并剥离内部标识");
        var retry=new TeamHurtInstallCore();
        Check(!retry.TryBegin(false,0) && retry.Attempts==0,"世界未就绪不消耗重试");
        Check(retry.TryBegin(true,0) && retry.Attempts==1,"首次就绪立即尝试");
        retry.Fail(false);
        Check(!retry.TryBegin(true,0.99),"失败不每帧重挂");
        Check(retry.TryBegin(true,1),"一秒后允许第二次");
        retry.Fail(false);
        Check(retry.TryBegin(true,2),"一秒后允许第三次");
        retry.Fail(false);
        Check(retry.Stopped && !retry.Installed && !retry.TryBegin(true,100),"三次失败后停止");
        retry.Reset();
        Check(retry.Attempts==0 && retry.TryBegin(true,0),"重读新世界可重新尝试");
        retry.Succeed();
        Check(retry.Installed && retry.Stopped && !retry.TryBegin(true,10),"成功后不再挂载");
        retry.Reset();retry.TryBegin(true,0);retry.Fail(true);
        Check(retry.Fatal && retry.Stopped && !retry.TryBegin(true,1),"类型初始化失败不无效重试");
        Console.WriteLine(count+" 项队友战斗身份与结算检查通过");
    }
}
