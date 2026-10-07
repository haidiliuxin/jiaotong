"""Export figures only after independent full turn-access verification."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.plot_experiment import plt, save


def plot(job):
    state=json.loads((job/'state.json').read_text(encoding='utf-8'))
    verified=json.loads((job/'verification.json').read_text(encoding='utf-8'))
    if state['status']!='completed' or not verified['passed']:
        raise ValueError('需要已完成并独立核验的完整禁转配对')
    output=job/'figures'
    output.mkdir(exist_ok=True)
    response=state['runs'][1]['response']
    modes=['car','taxi','motor','truck']
    labels=['小汽车','出租车','摩托车','货车']
    fig,ax=plt.subplots(figsize=(9,4.8),layout='constrained')
    bars=ax.bar(range(4),[response[m]['before'] for m in modes],color=['#15818c']*3+['#a5afb5'],width=.55,label='新匹配基准')
    ax.scatter(range(4),[response[m]['after'] for m in modes],marker='x',s=65,color='#c77934',label='新增禁转',zorder=3)
    for bar,mode in zip(bars,modes):
        q=response[mode]['before']
        ax.text(bar.get_x()+bar.get_width()/2,q+7,f'{q:.3f} → 0' if q>1 else '基准为0\n无正流量响应证据',ha='center',va='bottom',fontsize=10)
    ax.set_xticks(range(4),labels)
    ax.set_ylim(-15,max(r['before'] for r in response.values())*1.22)
    ax.set_ylabel('车型转移流量（原生五轮累加，分别呈现）')
    ax.set_title('4797→5990→5991｜完整禁转响应')
    ax.legend(loc='upper right')
    ax.grid(axis='y',alpha=.12)
    fig.supxlabel('3场景 / 21次顶层调用 / 60次适配求解 + 60次同输入原版参考；不合计不同车型作为收益。',fontsize=9)
    save(fig,output,'禁转四车型完整响应')
    if verified.get('supplementary_metrics_verified'):
        assessment=json.loads((job/'supplementary_assessment.json').read_text(encoding='utf-8'))['comparison']
        values=[assessment['delta'][scope]['running_time_change_pct'] for scope in ['core','neighbor','network']]
        fig,ax=plt.subplots(figsize=(9,4.8),layout='constrained')
        bars=ax.barh(['核心：1条道路','邻区：25条道路','全网：32,060条道路'],values,color=['#15818c','#487d90','#6b5d98'])
        ax.invert_yaxis()
        for bar,value in zip(bars,values):
            ax.text(value-1,bar.get_y()+bar.get_height()/2,f'{value:.4f}%',va='center',ha='right')
        ax.set_xlim(min(values)*1.3,2)
        ax.axvline(0,color='#8c9aa1',linewidth=1)
        ax.set_xlabel('ΣqL/v相对本轮新基准的变化（%）')
        ax.set_title('禁转接口配对后的补充指标比较')
        ax.grid(axis='x',alpha=.12)
        fig.supxlabel('沿用旧实验固定范围；新增邻区过饱和里程0km。尚未测S1/S3和需求组合，不等同正式推荐。',fontsize=9)
        save(fig,output,'禁转配对补充指标')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('job',type=Path)
    plot(parser.parse_args().job.resolve())
