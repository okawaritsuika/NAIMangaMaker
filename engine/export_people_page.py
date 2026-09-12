

def layout_rows(panels):
    rows=[]
    for index,panel in enumerate(panels):
        if panel['size']=='small' and rows and len(rows[-1])==1 and panels[rows[-1][0]]['size']=='small':
            rows[-1].append(index)
        else: rows.append([index])
    weights=[.7 if all(panels[i]['size']=='small' for i in row) else 1 for row in rows]
    total=sum(weights)
    cursor=0
    centers={}
    labels=[]
    for row,weight in zip(rows,weights):
        y=(cursor+weight/2)/total
        cursor+=weight
        labels.append('two small panels' if len(row)==2 else 'one wide panel')
        for col,i in enumerate(row): centers[i]=((col+.5)/len(row),y,1/len(row))
    return 'comic, '+str(len(panels))+' panels, '+', then '.join(labels)+' from top to bottom',centers
