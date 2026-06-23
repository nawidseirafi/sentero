type SenteroRoom = {
  id: string;
  name: string;
  status?: 'quiet' | 'active' | 'resting';
  summary?: string;
  lastSeen?: string;
};

export function RoomMap({ rooms, variant = 'cards' }: { rooms: SenteroRoom[]; variant?: 'cards' | 'floorplan' }) {
  if (variant === 'floorplan') {
    return (
      <div className="sc-floorplan" aria-label="Wohnungsansicht">
        {rooms.map((room) => (
          <div key={room.id} className={`sc-floorplan-room ${room.id} ${room.status}`}>
            <RoomLabel room={room} />
          </div>
        ))}
      </div>
    );
  }

  return <div className="sc-room-map">{rooms.map((room) => <RoomCard room={room} key={room.id} />)}</div>;
}

export function RoomCard({ room }: { room: SenteroRoom }) {
  return (
    <article className={`sc-room-card ${room.status}`}>
      <div>
        <span className="sc-room-dot" />
        <strong>{room.name}</strong>
      </div>
      <p>{room.summary || ''}</p>
      <small>{room.lastSeen || ''}</small>
    </article>
  );
}

function RoomLabel({ room }: { room: SenteroRoom }) {
  return (
    <span>
      {room.status === 'active' && <i />}
      {room.name}
    </span>
  );
}
