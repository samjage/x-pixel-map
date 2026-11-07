# Offline, read-only: predict cls / flag / aux for a pixel from its list coordinates and score against the camera's own records.
import collections
CLS={(0,0):5,(0,1):0,(0,2):1,(1,0):2,(1,1):7,(1,2):6,(2,0):4,(2,1):0,(2,2):1}
FLAG_POS={(0,1),(0,4),(2,2),(2,5),(3,1),(3,4),(5,2),(5,5)}   # (x%6,y%6), only for green-block codes 0/1
REG=(1400,3500,950,2350)
def predict_cls(x,y): return CLS[(x%3,y%3)]
def predict_flag(x,y):
    return 1 if predict_cls(x,y) in (0,1) and (x%6,y%6) in FLAG_POS else 0
def build_aux_table(records):
    c=collections.defaultdict(collections.Counter)
    for x,y,aux in records:
        if REG[0]<=x<=REG[1] and REG[2]<=y<=REG[3]: c[(x%6,y%12)][aux]+=1
    return {k:v.most_common(1)[0][0] for k,v in c.items()}
def predict_aux(x,y,table):
    if REG[0]<=x<=REG[1] and REG[2]<=y<=REG[3]: return table.get((x%6,y%12),0)
    return 0
def pack(x,y,cls,aux,flag): return x|(cls<<13)|(y<<17)|(aux<<30)|(flag<<33)
if __name__=='__main__':
    d=open('camera/cfgdata_dump_BACKUP_original.bin','rb').read()
    def recs(off,n):
        buf=d[off:off+(n*34+7)//8+8]; v=int.from_bytes(buf,'little')
        return [(v>>(i*34))&((1<<34)-1) for i in range(n)]
    dec=lambda r:(r&0x1fff,(r>>13)&15,(r>>17)&0x1fff,(r>>30)&7,(r>>33)&1)
    sets={'list2A':recs(0x11c840,17503),'list0A':recs(0x76800,3071)}
    R2=[dec(r) for r in sets['list2A']]
    table=build_aux_table([(x,y,a) for x,c,y,a,f in R2])
    for nm,rs in sets.items():
        R=[dec(r) for r in rs]
        ok_cls=sum(1 for x,c,y,a,f in R if predict_cls(x,y)==c)
        ok_flag=sum(1 for x,c,y,a,f in R if predict_flag(x,y)==f)
        ok_aux=sum(1 for x,c,y,a,f in R if predict_aux(x,y,table)==a)
        print(nm,'n',len(R),'cls %.4f flag %.4f aux %.4f'%(ok_cls/len(R),ok_flag/len(R),ok_aux/len(R)))
    # full-record reconstruction rate (all of cls,flag,aux right)
    full=sum(1 for x,c,y,a,f in R2 if (predict_cls(x,y),predict_aux(x,y,table),predict_flag(x,y))==(c,a,f))
    print('list2A full-record prediction rate %.4f'%(full/len(R2)))
    print('--- predicted fields for the three known hot pixels (RAF coords; list x = raf_x-4). Not written anywhere.')
    for rx,ry in ((2497,1861),(2317,1649),(470,2352)):
        x=rx-4; y=ry; c=predict_cls(x,y); f=predict_flag(x,y); a=predict_aux(x,y,table)
        print((rx,ry),'list x',x,'cls',c,'flag',f,'aux',a,'raw34=%09x'%pack(x,y,c,a,f))
