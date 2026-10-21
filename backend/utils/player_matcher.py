import unicodedata
import re
from rapidfuzz import process, fuzz, utils

class PlayerMatcher:
    def __init__(self, players_metadata):
        """
        Initialize with a list of player metadata dictionaries.
        Each dict should have: {'PLAYER_ID': int, 'PLAYER_NAME': str, 'TEAM_ABBREVIATION': str}
        """
        self.players_map = {} # Normalized Name -> List of {id, name, team}
        self.id_map = {}      # ID -> Player Data
        
        # Manual Aliases (Normalize these keys too!)
        # Nickname pairs are deliberately listed in BOTH directions (e.g.
        # "nic claxton" <-> "nicolas claxton") so that a prop row spelled either
        # way resolves. Which side the NBA stats feed treats as canonical is not
        # something this table should assume -- as of the 2025-26 feed it is the
        # short form ("Nic Claxton", "Mo Bamba", "Alex Sarr", "Bub Carrington"),
        # which is the opposite direction from several entries below. match_player
        # therefore resolves one step and then prefers whichever spelling actually
        # exists in players_map, which keeps the lookup direction-agnostic.
        self.aliases = {
            "nic claxton": "nicolas claxton",
            "kj martin": "kenyon martin",
            "mo bamba": "mohamed bamba",
            "cam thomas": "cameron thomas",
            "chuma okeke": "chukwuma okeke",
            "guillermo hernangomez": "willy hernangomez",
            "juancho hernangomez": "juan hernangomez",
            "xnba": "unknown", 
            "alexandre sarr": "alex sarr",
            "nicolas claxton": "nic claxton",
            "cameron thomas": "cam thomas",
            "carlton carrington": "bub carrington",
        }

        # Build Lookups
        for p in players_metadata:
            pid = p.get('PLAYER_ID')
            raw_name = p.get('PLAYER_NAME', '')
            team = p.get('TEAM_ABBREVIATION', 'UNK')
            
            norm = self.normalize_name(raw_name)
            
            entry = {
                'id': pid,
                'name': raw_name,
                'team': team,
                'norm_name': norm
            }
            
            self.id_map[pid] = entry
            
            if norm not in self.players_map:
                self.players_map[norm] = []
            self.players_map[norm].append(entry)

    def normalize_name(self, name):
        """Standardizes names: lowercase, ascii, no punctuation, optional suffix removal."""
        if not name or not isinstance(name, str):
            return ""
        
        # 1. Lowercase + Strip
        name = name.lower().strip()
        
        # 2. ASCII normalization (Jokić -> Jokic)
        name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
        
        # 3. Remove Punctuation (.,')
        name = name.replace('.', '').replace("'", "")
        
        # 4. Remove Suffixes (Jr, Sr, III, IV)
        # Note: Be careful not to remove "James" from "LeBron James" if I just replace "mes"
        # Use regex for whole word suffix
        suffixes = r'\b(jr|sr|ii|iii|iv|v)\b'
        name = re.sub(suffixes, '', name).strip()
        
        return name

    def match_player(self, raw_name, team_context=None, team_options=None):
        """
        Matches a raw name to a database player ID.
        
        Args:
            raw_name (str): The name from the sportsbook.
            team_context (str): The specific team of the player (e.g., 'BOS').
            team_options (list): A list of possible teams (e.g., ['BOS', 'LAL'] for a matchup).
        
        Returns:
            int: PLAYER_ID or None
        """
        norm_input = self.normalize_name(raw_name)
        
        # Check Aliases.
        # Aliases are applied exactly once -- a "resolve until fixpoint" loop would
        # spin forever on the two-way nickname pairs above. Because the table holds
        # both directions, substituting unconditionally can map an already-canonical
        # spelling *away* from players_map, and the fallback fuzzy tier does not save
        # it (fuzz.ratio("nicolas claxton", "nic claxton") is ~84, under the 85
        # cutoff). So prefer whichever of the two spellings the stats feed actually
        # uses, which makes the lookup direction-agnostic.
        alias_target = self.aliases.get(norm_input)
        if alias_target:
            if alias_target in self.players_map:
                norm_input = alias_target
            elif norm_input not in self.players_map:
                # Neither spelling is a known player; fall through on the alias,
                # as before, so the fuzzy tiers work off the canonical form.
                norm_input = alias_target

        # 1. EXACT MATCH
        if norm_input in self.players_map:
            candidates = self.players_map[norm_input]
            # Disambiguate if multiple players have same name (rare, but happens like Jalen/Jaylin Williams)
            if len(candidates) > 1:
                return self._disambiguate_by_team(candidates, team_context, team_options)
            return candidates[0]['id']

        # 2. FUZZY MATCH
        # Get all normalized keys
        all_names = list(self.players_map.keys())
        
        # Use simple ratio first (fast)
        # score_cutoff=85 means fairly strict
        match = process.extractOne(norm_input, all_names, scorer=fuzz.ratio, score_cutoff=85)
        
        if match:
            matched_name, score, _ = match
            candidates = self.players_map[matched_name]
            
            # TEAM CHECK for Tier 2 fuzzy matches (lower confidence requiring team match).
            # The input did not match exactly, so an 85-similar name can still be a
            # different player ("jalen smith" vs "jaden smith"); attaching that ID
            # would hang the prop line on the wrong player. If the row carries usable
            # team info it therefore has to agree -- whether there is one candidate
            # or several. Routing the multi-candidate case through
            # _disambiguate_by_team instead would reintroduce exactly this bug,
            # because that helper falls back to candidates[0] on disagreement.
            # _required_teams distinguishes "no team info given" from "team info
            # given but it disagrees": with no team info there is nothing to
            # contradict the match, and rejecting would silently drop legitimate props.
            if self._required_teams(team_context, team_options):
                team_match = self._check_team_match(candidates, team_context, team_options)
                return team_match['id'] if team_match else None

            # No usable team info. Same-normalized-name collisions are rare and
            # unresolvable here, so take the first candidate as before.
            return candidates[0]['id']
            
        # 3. LOOSE MATCH w/ TEAM CONTEXT
        # If we didn't find a strong match, try looser match BUT strictly require team agreement
        match_loose = process.extractOne(norm_input, all_names, scorer=fuzz.token_sort_ratio, score_cutoff=70)
        
        if match_loose:
            matched_name, score, _ = match_loose
            candidates = self.players_map[matched_name]
            
            # Check if ANY candidate matches the provided team context
            best_candidate = self._check_team_match(candidates, team_context, team_options)
            if best_candidate:
                return best_candidate['id']
                
        return None

    def _disambiguate_by_team(self, candidates, team_context, team_options):
        """Returns the ID of the candidate matching the team, or the first one if no team match/info."""
        match = self._check_team_match(candidates, team_context, team_options)
        if match:
            return match['id']
        return candidates[0]['id'] # Default to first found

    def _required_teams(self, team_context, team_options):
        """The teams a row claims the player is on, ignoring 'UNK' placeholders.

        An empty set means "no usable team info", which callers must treat
        differently from "team info that disagrees".
        """
        required_teams = set()
        if team_context and team_context != 'UNK':
            required_teams.add(team_context)
        if team_options:
            for t in team_options:
                if t and t != 'UNK': required_teams.add(t)
        return required_teams

    def _check_team_match(self, candidates, team_context, team_options):
        """Helper to find a candidate matching the team info."""
        required_teams = self._required_teams(team_context, team_options)
        if not required_teams:
            return None

        # Check candidates
        for cand in candidates:
            if cand['team'] in required_teams:
                return cand
        
        return None
