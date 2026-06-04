from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
import json, os, copy, secrets
from datetime import datetime

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)  # clé secrète pour les sessions

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
# En production (Railway) : data dans le même dossier que l'app
DATA_FILE = os.environ.get('DATA_FILE', os.path.join(BASE_DIR, 'radio_data.json'))
USERS_FILE= os.path.join(BASE_DIR, 'users.json')

# ── Gestion utilisateurs ───────────────────────────────────────────────────

def load_users():
    if os.path.isfile(USERS_FILE):
        with open(USERS_FILE) as f: return json.load(f)
    # Crée un admin par défaut si le fichier n'existe pas
    users = {'admin': {'password': generate_password_hash('Admin2026!'),
                        'role': 'admin', 'nom': 'Administrateur'}}
    with open(USERS_FILE,'w') as f: json.dump(users,f,indent=2)
    return users

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

# ── Routes login ───────────────────────────────────────────────────────────

@app.route('/login', methods=['GET','POST'])
def login():
    if 'username' in session:
        return redirect('/')
    error = None
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        users    = load_users()
        if username in users and check_password_hash(users[username]['password'], password):
            session['username'] = username
            session['role']     = users[username].get('role','user')
            session['nom']      = users[username].get('nom', username)
            next_url = request.args.get('next','/')
            return redirect(next_url)
        error = 'Identifiant ou mot de passe incorrect.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin/users', methods=['GET','POST'])
@login_required
def admin_users():
    if session.get('role') != 'admin':
        return "Acces refuse", 403
    users = load_users()
    msg   = None
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            uname = request.form.get('username','').strip()
            pwd   = request.form.get('password','')
            role  = request.form.get('role','user')
            nom   = request.form.get('nom','').strip()
            if uname and pwd and uname not in users:
                users[uname] = {'password': generate_password_hash(pwd),
                                 'role': role, 'nom': nom or uname}
                with open(USERS_FILE,'w') as f: json.dump(users,f,indent=2)
                msg = f"Utilisateur '{uname}' cree avec succes !"
            elif uname in users:
                msg = "Cet identifiant existe deja."
        elif action == 'delete':
            uname = request.form.get('username')
            if uname and uname != session['username'] and uname in users:
                del users[uname]
                with open(USERS_FILE,'w') as f: json.dump(users,f,indent=2)
                msg = f"Utilisateur '{uname}' supprime."
        elif action == 'change_pwd':
            uname = request.form.get('username')
            pwd   = request.form.get('password','')
            if uname and pwd and uname in users:
                users[uname]['password'] = generate_password_hash(pwd)
                with open(USERS_FILE,'w') as f: json.dump(users,f,indent=2)
                msg = f"Mot de passe de '{uname}' mis a jour."
    return render_template('admin_users.html', users=users, msg=msg,
                           current_user=session['username'])

PALIERS = [900, 1300, 1450]
STATUTS = ['Planifie','En cours','Realise','Annule']
STATUTS_Q = ['Ouverte','En cours','Cloturee','Reportee']
PRIORITES = ['Haute','Moyenne','Basse']
ZONES = ['Zone verte (<0.5 mSv/h)','Zone jaune (0.5-2 mSv/h)',
         'Zone orange (2-7.5 mSv/h)','Zone rouge (>7.5 mSv/h)']

_history = []

def load():
    default = {'activites':[],'actions':[],'activites_ref':[],
                'objectifs':{'dose_collective_max':5.0,'dose_indiv_max':2.0,'seuil_alerte':0.8}}
    if os.path.isfile(DATA_FILE):
        with open(DATA_FILE,'r',encoding='utf-8') as f:
            d = json.load(f)
        if 'activites_ref' not in d: d['activites_ref'] = []
        return d
    return default

def save(d):
    with open(DATA_FILE,'w',encoding='utf-8') as f:
        json.dump(d,f,ensure_ascii=False,indent=2)

def push_history(label='action'):
    _history.append((label, copy.deepcopy(load())))
    if len(_history) > 10: _history.pop(0)

def stats(data):
    res = {}
    for p in PALIERS:
        acts = [a for a in data['activites'] if a['palier']==p and a.get('statut')=='Realise']
        if acts:
            res[p] = {
                'nb': len(acts),
                'dose_totale': round(sum(a['dose_collective'] for a in acts),2),
                'dose_moy':    round(sum(a['dose_collective'] for a in acts)/len(acts),2),
                'dose_max':    round(max(a['dose_collective'] for a in acts),2),
                'dose_indiv':  round(max(a.get('dose_indiv_max',0) for a in acts),3),
                'duree':       sum(a.get('duree',0) for a in acts),
            }
        else:
            res[p] = {'nb':0,'dose_totale':0,'dose_moy':0,'dose_max':0,'dose_indiv':0,'duree':0}
    return res

# ─── ROUTES ────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    data = load()
    s = stats(data)
    acts_r = [a for a in data['activites'] if a.get('statut')=='Realise']
    seuil  = data['objectifs']['dose_collective_max']
    top5   = sorted(acts_r, key=lambda a:a['dose_collective'], reverse=True)[:5]
    kpis = {
        'dose_totale': round(sum(a['dose_collective'] for a in acts_r),2),
        'nb_activites': len(acts_r),
        'dose_max': round(max((a['dose_collective'] for a in acts_r),default=0),2),
        'nb_critiques': len([a for a in acts_r if a['dose_collective']>seuil]),
        'nb_qsser': len([a for a in data['actions'] if a.get('statut') in ('Ouverte','En cours')]),
        'nb_ref': len(data['activites_ref']),
    }
    return render_template('dashboard.html', kpis=kpis, stats=s, top5=top5,
                           seuil=seuil, data=data, paliers=PALIERS,
                           can_undo=len(_history)>0,
                           last_action=_history[-1][0] if _history else '')

@app.route('/donnees')
@login_required
def donnees():
    data = load()
    palier = request.args.get('palier','')
    statut = request.args.get('statut','')
    search = request.args.get('q','').lower()
    tri    = request.args.get('tri','dose_desc')
    acts   = data['activites']
    if palier: acts = [a for a in acts if str(a['palier'])==palier]
    if statut: acts = [a for a in acts if a.get('statut','')==statut]
    if search: acts = [a for a in acts if search in a['nom'].lower() or search in a.get('gamme','').lower()]
    tri_map = {
        'dose_desc': (lambda a:a['dose_collective'], True),
        'dose_asc':  (lambda a:a['dose_collective'], False),
        'nom_asc':   (lambda a:a['nom'].lower(), False),
        'nom_desc':  (lambda a:a['nom'].lower(), True),
        'date_desc': (lambda a:a.get('date',''), True),
        'date_asc':  (lambda a:a.get('date',''), False),
    }
    fn,rev = tri_map.get(tri,(lambda a:a['dose_collective'],True))
    acts = sorted(acts,key=fn,reverse=rev)
    seuil = data['objectifs']['dose_collective_max']
    return render_template('donnees.html', activites=acts, seuil=seuil,
                           paliers=PALIERS, statuts=STATUTS, zones=ZONES,
                           ref_noms=[r['nom'] for r in data['activites_ref']],
                           filter_palier=palier, filter_statut=statut,
                           search=search, tri=tri,
                           total_dc=round(sum(a['dose_collective'] for a in acts),2),
                           nb_critiques=len([a for a in acts if a['dose_collective']>seuil]),
                           can_undo=len(_history)>0,
                           last_action=_history[-1][0] if _history else '')

@app.route('/referentiel')
@login_required
def referentiel():
    data = load()
    refs = data.get('activites_ref',[])
    for r in refs:
        r['_nb'] = sum(1 for a in data['activites'] if a['nom']==r['nom'])
    return render_template('referentiel.html', refs=refs,
                           can_undo=len(_history)>0,
                           last_action=_history[-1][0] if _history else '')

@app.route('/analyse')
@login_required
def analyse():
    data = load()
    acts_r = [a for a in data['activites'] if a.get('statut')=='Realise']
    noms = list(dict.fromkeys(a['nom'] for a in data['activites']))
    par_activite = {}
    for nom in noms:
        par_activite[nom] = {}
        for p in PALIERS:
            vals = [a['dose_collective'] for a in acts_r if a['nom']==nom and a['palier']==p]
            par_activite[nom][p] = round(sum(vals)/len(vals),2) if vals else None
    return render_template('analyse.html', noms=noms, par_activite=par_activite,
                           paliers=PALIERS, seuil=data['objectifs']['dose_collective_max'])

@app.route('/comparaison')
@login_required
def comparaison():
    data = load()
    s = stats(data)
    acts_r = [a for a in data['activites'] if a.get('statut')=='Realise']
    noms = list(dict.fromkeys(a['nom'] for a in data['activites']))
    tableau = []
    for nom in noms:
        row = {'nom':nom}
        for p in PALIERS:
            vals = [a['dose_collective'] for a in acts_r if a['nom']==nom and a['palier']==p]
            row[p] = round(sum(vals)/len(vals),2) if vals else None
        row['tendance'] = ''
        if row[900] and row[1450]:
            row['tendance'] = 'hausse' if row[1450]>row[900] else 'stable'
        tableau.append(row)
    return render_template('comparaison.html', tableau=tableau, stats=s,
                           paliers=PALIERS, seuil=data['objectifs']['dose_collective_max'])

@app.route('/axes')
@login_required
def axes():
    data = load()
    seuil = data['objectifs']['dose_collective_max']
    acts_r = [a for a in data['activites'] if a.get('statut')=='Realise']
    critiques = sorted([a for a in acts_r if a['dose_collective']>seuil],
                       key=lambda a:a['dose_collective'], reverse=True)
    def recommande(a):
        if a.get('nb_intervenants',0)>6: return 'Reduire le nb d intervenants (rotation optimisee)'
        if a.get('duree',0)>16: return 'Reviser la gamme pour reduire la duree'
        if a['palier']==1450: return 'Mettre en place un ecran de protection supplementaire'
        return 'Analyser le REX et optimiser la gamme operatoire'
    for a in critiques: a['_rec'] = recommande(a)
    top10 = sorted(acts_r, key=lambda a:a['dose_collective'], reverse=True)[:10]
    return render_template('axes.html', critiques=critiques, top10=top10,
                           seuil=seuil, paliers=PALIERS)

@app.route('/qsser')
@login_required
def qsser():
    data = load()
    filtre = request.args.get('statut','')
    priorite = request.args.get('priorite','')
    acts = data['actions']
    if filtre:   acts = [a for a in acts if a.get('statut','')==filtre]
    if priorite: acts = [a for a in acts if a.get('priorite','')==priorite]
    stats_q = {
        'total':    len(data['actions']),
        'ouvertes': len([a for a in data['actions'] if a.get('statut')=='Ouverte']),
        'en_cours': len([a for a in data['actions'] if a.get('statut')=='En cours']),
        'clotures': len([a for a in data['actions'] if a.get('statut')=='Cloturee']),
        'gain':     round(sum(a.get('gain_estime',0) for a in data['actions'] if a.get('statut')=='Cloturee'),2),
    }
    return render_template('qsser.html', actions=acts, stats=stats_q,
                           statuts=STATUTS_Q, priorites=PRIORITES,
                           filter_statut=filtre, filter_priorite=priorite,
                           can_undo=len(_history)>0,
                           last_action=_history[-1][0] if _history else '')

# ─── API ───────────────────────────────────────────────────────────────────

@app.route('/api/activites', methods=['POST'])
@login_required
def add_activite():
    push_history(f"Ajout : {request.json.get('nom','')[:30]}")
    data = load()
    act  = request.json
    ids  = [a['id'] for a in data['activites']]
    act['id'] = max(ids)+1 if ids else 1
    data['activites'].append(act); save(data)
    return jsonify({'ok':True,'id':act['id']})

@app.route('/api/activites/<int:id>', methods=['PUT'])
def edit_activite(id):
    data = load()
    a = next((x for x in data['activites'] if x['id']==id),None)
    if a: push_history(f"Modification : {a['nom'][:30]}")
    for x in data['activites']:
        if x['id']==id: x.update(request.json); break
    save(data); return jsonify({'ok':True})

@app.route('/api/activites/<int:id>', methods=['DELETE'])
def del_activite(id):
    data = load()
    a = next((x for x in data['activites'] if x['id']==id),None)
    if a: push_history(f"Suppression : {a['nom'][:30]}")
    data['activites'] = [x for x in data['activites'] if x['id']!=id]
    save(data); return jsonify({'ok':True})

@app.route('/api/referentiel', methods=['POST'])
@login_required
def add_ref():
    push_history(f"Ajout ref : {request.json.get('nom','')[:30]}")
    data = load()
    refs = data.setdefault('activites_ref',[])
    nom  = request.json.get('nom','').strip()
    if any(r['nom']==nom for r in refs): return jsonify({'ok':False,'msg':'Existe deja'})
    refs.append({'nom':nom,'gamme':request.json.get('gamme',''),'description':request.json.get('description','')})
    save(data); return jsonify({'ok':True})

@app.route('/api/referentiel/<path:nom>', methods=['DELETE'])
def del_ref(nom):
    push_history(f"Suppression ref : {nom[:30]}")
    data = load()
    data['activites_ref'] = [r for r in data.get('activites_ref',[]) if r['nom']!=nom]
    save(data); return jsonify({'ok':True})

@app.route('/api/qsser', methods=['POST'])
@login_required
def add_qsser():
    push_history(f"Ajout QSSER : {request.json.get('titre','')[:30]}")
    data = load()
    a    = request.json
    ids  = [x['id'] for x in data['actions']]
    a['id'] = max(ids)+1 if ids else 1
    a['date_creation'] = datetime.now().strftime('%Y-%m-%d')
    data['actions'].append(a); save(data)
    return jsonify({'ok':True,'id':a['id']})

@app.route('/api/qsser/<int:id>', methods=['PUT'])
def edit_qsser(id):
    push_history(f"Modification QSSER #{id}")
    data = load()
    for x in data['actions']:
        if x['id']==id: x.update(request.json); break
    save(data); return jsonify({'ok':True})

@app.route('/api/qsser/<int:id>', methods=['DELETE'])
def del_qsser(id):
    push_history(f"Suppression QSSER #{id}")
    data = load()
    data['actions'] = [x for x in data['actions'] if x['id']!=id]
    save(data); return jsonify({'ok':True})

@app.route('/api/undo', methods=['POST'])
@login_required
def undo():
    if not _history: return jsonify({'ok':False,'msg':'Rien a annuler'})
    label,previous = _history.pop()
    save(previous)
    return jsonify({'ok':True,'msg':f'Annule : {label}'})

@app.route('/api/reset', methods=['POST'])
@login_required
def reset():
    push_history('Reinitialisation complete')
    mode = request.json.get('mode','vide')
    if mode=='vide':
        data = load(); data['activites']=[]; data['actions']=[]
        save(data)
    else:
        import sys; sys.path.insert(0,'..')
        from radioprotection_gmpp import generate_sample_data
        save(generate_sample_data())
    return jsonify({'ok':True})

@app.route('/api/stats')
@login_required
def api_stats():
    return jsonify(stats(load()))

if __name__ == '__main__':
    port = int(os.environ.get('PORT',5002))
    app.run(host='0.0.0.0',port=port,debug=False)
